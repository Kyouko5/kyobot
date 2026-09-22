# Context Manager 设计：优先级、预算、修复与压缩（Phase 6）

- 状态：已实现（Phase 6）
- 关联文档：[`docs/context.md`](./context.md)（Phase 1 对上游上下文机制的理解）、
  [`docs/design.md`](./design.md)（§2 调用链、§3.8 契约、§6 答辩）、
  [`docs/decision-records/0010-context-budget.md`](./decision-records/0010-context-budget.md)（预算公式与配额）、
  [`docs/records/phase-6-context.md`](./records/phase-6-context.md)（实验与质量门）
- 代码入口：`src/myagent/agent/context.py`（section 模型 + 四步拟合）、
  `src/myagent/agent/compaction.py`（摘要检查点）、`src/myagent/agent/token_budget.py`（token 估算）；
  装配点 `src/myagent/runtime.py:47`（`build_agent`）、CLI 入口 `src/myagent/cli.py:479`（`chat`）与
  `src/myagent/cli.py:304`（`session compact`）

## 0. 一句话

Phase 5 结束时，Context 已经能把记忆与文档拼进 prompt，但**超预算只能报错**：
上游把「拼装」与「拟合预算」拆在 `ContextBuilder` / `ContextGovernor` 两个文件里
（`nanobot/nanobot/agent/context.py:89`、`nanobot/nanobot/agent/context_governance.py:336`），
我们合并成一个 `ContextManager`，并把它的职责收敛成**四步、有固定顺序**的一次拟合：

```text
1. 按 section 配额裁剪（超配额再按优先级从大到小继续降级）
2. 删除孤儿 tool 结果        3. 补上缺失的 tool 结果    ← 修结构，不省 token
4. 校验：装不下就抛 ContextWindowExceeded（不静默截断）
```

三条结论（各自的证据在 §4、§5、§6）：

1. **Context 太长时先裁剪、再修结构、最后才报错**——顺序写死在
   `src/myagent/agent/context.py:497` 的 `build()` 里，因为裁剪本身会制造结构错误；
2. **降级顺序由一张优先级表决定**（tools 6 → rag 5 → memory 4 → summary 3 → conversation 2，
   `src/myagent/agent/context.py:98`），每次降级都写进 `ContextReport`
   （`src/myagent/agent/context.py:288`）并进日志，而不是藏在字符串拼接里；
3. **摘要压缩是「视图」而不是「删除」**：`myagent session compact` 只把重放游标
   `last_archived` 往前推（`src/myagent/session/manager.py:92`），原文永远留在 JSONL 里
   （`src/myagent/session/base.py:45`）。

## 1. 全景图：一次 `--show-context` 的请求经过哪些代码

```text
myagent chat -m "问题" --show-context
  └── src/myagent/cli.py:479 _chat
        ├── build_agent()                          src/myagent/runtime.py:47
        │      └── SectionedContextManager(        src/myagent/runtime.py:93
        │              budget=resolved_runtime.context_budget,   ← AgentRuntimeConfig.context_budget
        │              tokens=_token_counter(model))             ← 模型自己的计数（可能是 None）
        │          memory=build_memory(...)        src/myagent/runtime.py:103
        │          retriever=build_rag(...)        src/myagent/runtime.py:104
        └── AgentLoop.run_turn()                   src/myagent/agent/loop.py:150
              └── _build_turn()                    src/myagent/agent/loop.py:186
                    ├── MemoryProvider.recall      src/myagent/agent/loop.py:247（失败只记日志）
                    ├── DocumentProvider.recall    src/myagent/agent/loop.py:262（失败只记日志）
                    ├── ContextRequest(history=session.transcript(),
                    │                  summary=session.summary, ...)
                    └── ContextManager.build()     src/myagent/agent/context.py:497
                          ├── _clip_to_quotas()    src/myagent/agent/context.py:627   ① 配额
                          ├── _fit()               src/myagent/agent/context.py:696   ① 优先级
                          ├── _repair()            src/myagent/agent/context.py:729   ②③ 结构
                          └── 校验 + _report()     src/myagent/agent/context.py:756   ④ 报告
              → AgentRunner.run() → _save_turn() → _prepare_outbound()
  └── cli._context_transcript()                    src/myagent/cli.py:498 打印每段的 budget/used/dropped
```

压缩走的是另一条路（不在请求路径上，因为它要调用模型）：

```text
myagent session compact cli:default --keep-recent 2
  └── src/myagent/cli.py:304 _session_compact
        ├── compact_session(session, ModelSummarizer(model))   src/myagent/agent/compaction.py:129
        │     ├── boundary_for_turns(messages, 2)              src/myagent/agent/compaction.py:174
        │     └── ModelSummarizer.summarize(archived)          src/myagent/agent/compaction.py:112
        └── SessionStore.commit_summary(key, summary, boundary) src/myagent/session/manager.py:92
```

## 2. Context 的七段结构（PLAN 6.0）

每一段都是 Phase 3 定义的 `ContextSection`（`src/myagent/agent/context.py:223`）：
`name / priority / required / content / budget_tokens`，因此「加了什么、裁了什么」
在报告里可见，而不是藏在一次 f-string 拼接里。

| 段 | 优先级 | required | 类型 | 来源 | 超配额时的动作（PLAN 6.2） |
| --- | ---: | :-: | --- | --- | --- |
| `system`（含 Pinned） | 0 | ✅ | 文本 | `system_prompt()`（`src/myagent/agent/context.py:475`） | 永不裁剪 |
| `query` | 1 | ✅ | 消息 | `ContextRequest.user_input` | 永不裁剪 |
| `conversation` | 2 | ❌ | 消息 | `Session.transcript()` | 丢最旧的**整轮**（可能触发自动压缩） |
| `summary` | 3 | ❌ | 文本 | `Session.summary`（检查点） | 截断摘要 |
| `memory` | 4 | ❌ | 文本 | `MemoryProvider.recall`（Phase 4） | 先丢低重要度的记忆 |
| `rag` | 5 | ❌ | 文本 | `DocumentProvider.recall`（Phase 5） | 先丢低分的 chunk |
| `tools` | 6 | ❌ | 文本 | `ToolRegistry.get_definitions()` | 从尾部丢工具描述 |

要点：

- **空段不占位**：memory / rag / tools / summary 为空时不会生成 section
  （`src/myagent/agent/context.py:556` 起的分支），所以「关掉开关」的表现是
  **section 消失**而不是「一个有标题的空块」；
- **文本段合并成一条 system 消息**，消息段（conversation / query）按顺序展开，
  query 恒在最后（`src/myagent/agent/context.py:597` 的 `_assemble`）——
  这也是 `transcript_start = len(messages) - 1` 能成立的原因，
  Loop 只落盘 `messages[transcript_start:]`（`src/myagent/agent/loop.py:225`）；
- **Pinned 没有单独的段**：身份与工具契约本身就是不可裁的约束，所以它就在 `system` 段里
  （`src/myagent/agent/context.py:475` 的 docstring 说明了这个取舍）；
- **工具 schema 不在这段里**：这里只是给模型看的名字与一句话说明，真正的 JSON Schema 走
  `AgentRunSpec.tools`（`src/myagent/agent/context.py:954`），所以裁掉工具描述不会让工具不可调用。

## 3. 优先级：数字越小越先保留（PLAN 6.1）

`_PRIORITY_OF` 是唯一的顺序来源（`src/myagent/agent/context.py:98`），
`required` 只有 `system` / `query` 两个（`src/myagent/agent/context.py:107`；
Phase 3 的 `required` 字段在那个阶段就已经存在（`src/myagent/agent/context.py:223`），
Phase 6 才第一次真的用它拒绝裁剪）。降级动作从优先级最大的段开始，
一次只丢一个 item（一个 chunk / 一条记忆 / 一次摘要截断 / 最旧的一轮），
循环条件写在 `_fit`（`src/myagent/agent/context.py:696`）：

```python
while self._total(plan) > budget:
    if _drop_tool(plan): ...          # 6 tools
    if _drop_weakest(plan.chunks): ...   # 5 rag（先丢低分）
    if _drop_weakest(plan.memories): ... # 4 memory（先丢低重要度）
    if _truncate_summary(plan): ...      # 3 summary（每次砍 1/4）
    if _drop_turn(plan): ...             # 2 conversation（丢最旧整轮）
    raise ContextWindowExceeded(...)     # 只剩 required 也放不下
```

实测（`docs/records/phase-6-context.md` §6.1）：四个来源塞满、预算 400 token 时，
`tools` 先被砍到 0、`rag` 丢 3 块、`memory` 丢 4 条、`conversation` 压缩到 100 token，
顺序与上表一致；单元测试是 `tests/test_context.py::test_priority_order` 与
`tests/test_context.py::test_the_fit_loop_degrades_from_the_largest_priority_down`。

## 4. 预算：一个公式，一处实现（PLAN 6.2）

```text
input_budget = context_window_tokens - max_output_tokens - 1024（安全余量）
```

公式与安全余量只写在 `src/myagent/agent/runtime.py:30`、`:84`，
`AgentRuntimeConfig.context_budget`（`src/myagent/agent/runtime.py:57`）把它包成
`ContextBudget`，`build_agent` 读它（`src/myagent/runtime.py:95`）——`build()` 里没有任何常量。
`LLM_CONTEXT_WINDOW` 太小算不出正预算时，`context_budget_tokens` 保持 `None`，
语义是「不检查」，而不是「预算为 0」（`src/myagent/agent/runtime.py:72`）。

各来源的初始配额（`src/myagent/agent/context.py:111`，实验依据见 ADR-0010）：

| 来源 | 比例 | 说明 |
| --- | ---: | --- |
| Recent Conversation | 35% | `conversation_ratio`，超配额时先裁最旧轮次（§6.4 的自动触发） |
| RAG Context | 35% | `rag_ratio`，减少 chunk 数（先砍低分） |
| Relevant Memory | 20% | `memory_ratio`，减少记忆条数（先砍低重要度） |
| Other（Pinned/Summary/Tools） | 10% | `other_ratio`；`summary` 与 `tools` 共用这一档 |

`ContextBudget` 的三件事（`src/myagent/agent/context.py:348`）：

1. `quota(name)` 给出某段的 token 上限（`src/myagent/agent/context.py:380`），
   `None` 表示「无预算、不裁剪」；
2. `with_input_tokens(limit)` 让同一次请求的**配额**跟随实际预算
   （`src/myagent/agent/context.py:386`）——请求可以自带 `budget_tokens` 覆盖运行时配置；
3. `__post_init__` 拒绝 `input_tokens <= 0` 与越界比例（`src/myagent/agent/context.py:368`），
   配置错误在装配时就暴露。

每次 `build()` 都产出一份 `ContextReport`（`src/myagent/agent/context.py:288`）：
每段的 `budget / used / dropped / action`，外加总量与 `summary_line()`
（`src/myagent/agent/context.py:301`）。有降级动作时写一条 info 日志
（`src/myagent/agent/context.py:510`，Phase 9 会把它结构化）。CLI 用
`--show-context` 打印同一份报告（`src/myagent/cli.py:498`）。

## 5. 结构修复：顺序不能反（PLAN 6.3）

对齐上游 `fit_to_budget` 的四步（`nanobot/nanobot/agent/context_governance.py:336`、
`nanobot/nanobot/agent/context_governance.py:951`），我们的实现是
`src/myagent/agent/context.py:497` 里那四行调用：

1. `_clip_to_quotas` + `_fit`：按配额裁剪，超了再按优先级降级（§4、§3）；
2. `_drop_orphan_tool_results`（`src/myagent/agent/context.py:890`）：
   `tool` 消息要能对上前面 `assistant(tool_calls)` 里宣告过的 `call_id`；
3. `_backfill_missing_tool_results`（`src/myagent/agent/context.py:911`）：
   有 tool call 却没有结果时，补一条占位错误消息（`src/myagent/agent/context.py:120`
   的 `MISSING_TOOL_RESULT`），它插在该 call 自己的结果之后，不打断顺序；
4. 校验总量：`_measure`（`src/myagent/agent/context.py:612`）用的数字包含第 2、3 步的结果，
   装不下就抛 `ContextWindowExceeded`（`src/myagent/agent/context.py:137`）。

**为什么是「先修结构、再判大小」**：裁剪（第 1 步）本身就会制造这两种畸形——
丢掉一轮的一半就会留下孤儿结果，丢掉 assistant 消息就会让结果失去调用者。
如果先判大小再修结构，一个「看起来刚好」的请求会在 provider 那里变成 400；
反过来，第 3 步补的占位符是要花 token 的，所以它必须记在最终数字里
（`tests/test_context.py::test_a_backfill_that_no_longer_fits_is_refused` 就钉住这一点）。

**为什么不是「先删孤儿结果再删历史」**：孤儿结果不是「更小的请求」，而是**非法请求**，
它本来就不该被发出去——修它是零成本的正确性修复，不是省 token 的手段；
而删历史是**有损**的（丢掉真实发生过的事）。所以免费且严格正确的先做，
要付代价的（丢信息）后做，且每一次都记进报告。

**报错语义与 Phase 2 对齐**：`ContextWindowExceeded` 继承
`myagent.models.base.ContextWindowExceeded`（`src/myagent/agent/context.py:137`），
所以「我们算出来装不下」与「provider 拒绝」在调用方眼里是同一个错误族——
runner 按普通 `LLMError` 处理，Loop 在 build 阶段捕获它并把这一轮变成一句可读的
`The request was not sent: ...`（`src/myagent/agent/loop.py:201`、`src/myagent/agent/loop.py:296`），
而不是让异常穿透 CLI。错误文本里带上估算值与预算，并提示 `LLM_CONTEXT_WINDOW`
（`src/myagent/agent/context.py:151`）。

## 6. 压缩：三条触发路径，一条落地方式（PLAN 6.4）

| 触发 | 何时发生 | 谁做 | 现状 |
| --- | --- | --- | --- |
| 显式 | `myagent session compact <key>` | `compact_session` + `SessionStore.commit_summary` | ✅ `src/myagent/cli.py:304`、`src/myagent/agent/compaction.py:129` |
| 自动 | `conversation` 超 35% 配额 | `SectionedContextManager.compact`（只丢最旧轮，不调模型） | ✅ `src/myagent/agent/context.py:523`、`:643` |
| 空闲 | 会话空闲一段时间后 | 上游 `agent/autocompact.py:68` 的定时器 | ❌ **默认关闭**（没有调度器，见 §9） |

自动触发刻意**不重新生成摘要**：`build()` 在请求路径上，不能调用模型
（`src/myagent/agent/context.py:526` 的 docstring）。它只把最旧的整轮移出请求，
并用 `CompactionReport` 说明丢了什么（`src/myagent/agent/context.py:330`：
压缩前后 token、消息数、轮数、边界）。显式压缩才会真的调用一次模型，
把 `[last_archived, boundary)` 的对话换成一个检查点：

- `boundary_for_turns(messages, keep_recent_turns)`（`src/myagent/agent/compaction.py:174`）
  从后往前数第 N 个 user 消息的下标——边界永远落在一轮的起点，
  因此压缩不会像「按条数切」那样切断 tool call 与它的结果（`keep_recent_turns <= 0`
  表示「一轮都不留」，返回 `len(messages)`）；
- 摘要由 `ModelSummarizer`（`src/myagent/agent/compaction.py:93`）用**同一个聊天模型**
  一次无工具的调用生成，提示词要求保留名字/数字/日期/决定/偏好/文件路径/未完成的工作
  （`src/myagent/agent/compaction.py:64`）；空回答视为失败并抛 `LLMError`
  （`src/myagent/agent/compaction.py:125`），因为「空摘要」等于悄悄删掉整段对话；
- 摘要有自己的上限 `DEFAULT_SUMMARY_TOKENS = 600`（`src/myagent/agent/compaction.py:61`），
  超出就在生成处截断，使「报告里的 token 数」与「prompt 里真实的 token 数」一致；
- 重复压缩是**在上一次摘要之上叠加**：`start = min(session.last_archived, len(messages))`
  （`src/myagent/agent/compaction.py:143`）保证已归档的前缀不会被二次摘要；
- 游标只前移不后退：`commit_summary` 忽略更小的 boundary
  （`src/myagent/session/manager.py:92`），因为「压缩只能归档更多」是它的语义。

实测（`docs/records/phase-6-context.md` §6.2）：24 轮、保留最近 6 轮，
  请求从 937 token 降到 497 token（-47%），归档 18 轮，摘要 156 token；
3 个探针问题在压缩前后的回答都命中关键事实（`myagent_memories` / `800` / `128000`）。

## 7. 集成与开关（PLAN 6.5）

```python
# src/myagent/runtime.py:88
AgentLoop(
    model=resolved_model,
    tools=...,
    context=SectionedContextManager(                 # ← 预算来自运行时配置
        resolved.agent.workspace,
        budget=resolved_runtime.context_budget,      # src/myagent/runtime.py:95
        tokens=_token_counter(resolved_model),       # src/myagent/runtime.py:96
    ),
    sessions=resolved_sessions,
    runtime=resolved_runtime,
    memory=build_memory(resolved, ...),              # src/myagent/runtime.py:103
    retriever=build_rag(resolved),                   # src/myagent/runtime.py:104
)
```

两个开关（供 Phase 8 做 ON/OFF 对比）：

| 开关 | 关掉后发生什么 | 实现位置 |
| --- | --- | --- |
| `MYAGENT_RAG_ENABLED=false` | `RagPipeline.recall` 直接返回 `[]` → **RAG section 消失**；`ingest` / `search` / `docs` 照常工作 | `src/myagent/rag/pipeline.py:257`、`src/myagent/config/settings.py:137` |
| `MYAGENT_MEMORY_ENABLED=false` | `MemoryManager.recall` 返回 `[]` → **Memory section 消失**；`memory list` 仍能看到已有记录 | `src/myagent/memory/manager.py:126` |

两条边界是**类型强制**的，不是约定：

- `agent/` 不 import `memory/` 或 `rag/`，只认 `MemoryProvider`（`src/myagent/agent/context.py:180`）
  与 `DocumentProvider`（`src/myagent/agent/context.py:204`）两个 Protocol，
  所以 `ContextManager` 里既没有 `QdrantClient` 也没有 `sqlite3`
  （`tests/test_contracts.py` 的 AST 检查在核心模块上钉住这条规则）；
- 检索失败只降级、不失败：`_recall_documents`（`src/myagent/agent/loop.py:262`）与
  `_recall_memories`（`src/myagent/agent/loop.py:247`）把异常记成 warning 后返回 `[]`——
  Qdrant 掉线的代价是「这一轮少了引用」，不是「这一轮不能用」。

真实 transcript（`docs/records/phase-6-context.md` §7.1）里七个 section 同时出现，
`budget 122880 token(s), used 2719, dropped 0`。

## 8. 答辩

### 8.1 Context 太长怎么办？

按固定顺序做四件事，**先省 token、再保正确、最后才拒绝**：

1. **算预算**：`input_budget = context_window - max_output_tokens - 1024`
   （`src/myagent/agent/runtime.py:84`）。先留出输出空间与安全余量，
   否则请求会以「刚好塞满窗口」的姿态被 provider 拒绝，错误信息还很难读；
2. **按配额定额裁剪**（§4）：每段的降级动作不同——对话丢最旧轮、RAG 丢低分块、
   记忆丢低重要度、摘要截断、工具描述从尾部丢（`src/myagent/agent/context.py:627`）；
3. **超配额再按优先级降级**（§3）：从优先级最大的段开始，一次一个 item
   （`src/myagent/agent/context.py:696`）；
4. **修结构、再校验**：孤儿 tool 结果要删、缺失的结果要补，然后才用最终数字判断；
   实在装不下就抛 `ContextWindowExceeded`（`src/myagent/agent/context.py:137`），
   让「请求没发出去」成为一条可读的错误，而不是静默截断（截断会悄悄改变语义）
   或让 provider 返回 400。

如果这四步还不够，剩下的手段按「代价从小到大」：**摘要压缩**（把旧对话换成检查点，
实测 -47%）、**调大 `LLM_CONTEXT_WINDOW`**、**换更小的检索 top_k/记忆条数**。
摘要压缩之所以排在裁剪之后，是因为它要花一次模型调用（§6），
不能在请求路径上做；而裁剪是纯本地的、可预测的。

### 8.2 为什么先删孤儿 tool 结果，而不是先删历史对话？

因为这两件事**不在同一个轴上**：孤儿结果是把请求从「非法」修成「合法」，
不是省 token 的手段。

1. **孤儿结果本来就不该存在**：`tool` 消息必须对得上前面 `assistant(tool_calls)`
   宣告过的 `call_id`，否则 OpenAI 兼容端点直接 400。它带的 token 只是「顺带省下来」的，
   真正的问题是**请求结构错了**；
2. **删历史是「有损」操作**：对话里是真实发生过的事，丢了就是丢了（只能靠摘要补偿）。
   所以免费且严格正确的修复先做，要付信息代价的后做——
   `_fit` 的降级顺序（`src/myagent/agent/context.py:696`）把
   `conversation` 放在最后，也是同一个理由；
3. **顺序反了会掩盖错误**：裁剪本身会制造孤儿（丢掉一轮的一半、丢掉 assistant 消息），
   所以修复必须**在裁剪之后**（`src/myagent/agent/context.py:497` 的调用顺序）；
   如果反过来先修再裁，裁完又会出现新的孤儿，而那时已经不再修了。

一句话：**结构合法性优先于省 token**——宁可明确报错，也不要发出一个 provider 会拒绝的请求。

### 8.3 为什么摘要压缩要保留原文？

因为摘要是**模型生成的、有损的**，而原文是**事实**；把有损层做成不可逆的删除，
等于把「模型的一次判断」升级成「这段对话的唯一记录」。具体三个理由：

1. **摘要会丢东西，只是丢多少不确定**：我们的实验里 24 轮压成 156 token 时，
   3 个探针问题全部答对（`docs/records/phase-6-context.md` §6.2），
   但这是**被测出来的**，不是保证的——摘要长度只有原文的 1/5，
   关键信息能否留下取决于模型当次的表现；
2. **保留原文 = 可审计、可回放**：`commit_summary` 只把 `last_archived` 往前推
   （`src/myagent/session/manager.py:92`），`Session.transcript()` 从该游标开始回放
   （`src/myagent/session/base.py:45`），JSONL 是追加式文件，压缩只**多写一行 header**；
   `myagent session compact` 的输出会明确打印 `originals stay in the JSONL file`
   （真实输出见 `docs/records/phase-6-context.md` §7.2）；
3. **代价可接受**：原文是本地几万字符的 JSONL（一个会话约几十 KB），
   而丢掉的信息无法找回；项目还处在 Phase 6/7，把「压缩」做成**视图切换**
   而不是数据删除，也让后续阶段（Phase 8 的评测、Phase 9 的运维）可以随时
   重新解释同一份原始数据。

反过来说，如果将来确实要物理清理（例如隐私删除），那应该是**另一个显式命令**
（比如 `session forget`），而不是压缩的副作用。

## 9. 已知限制与明确不做

| 限制 | 现状 | 位置 |
| --- | --- | --- |
| token 计数是估算，不是分词器 | `BaseModel.count_tokens()` 仍返回 `None`（契约已留口，`tokens.py` 的 4 字符≈1 token 是当前唯一的尺子） | `src/myagent/models/openai_compat.py:119`、`src/myagent/tokens.py:37` |
| 空闲压缩（autocompact）未实现 | PLAN 6.4 明确「默认关闭」：没有定时器/后台任务，压缩只由 CLI 或配额触发 | §6 的表 |
| 摘要上限是常量 | `DEFAULT_SUMMARY_TOKENS = 600` 不随窗口缩放；窗口很小的模型上摘要仍可能超 10% 的 other 配额，那时会被截断（并记进报告） | `src/myagent/agent/compaction.py:61`、`src/myagent/agent/context.py:670` |
| 摘要没有二次校验 | 只校验「非空」；把摘要与原文对照检查（或让模型自评）留给 Phase 8 | `src/myagent/agent/compaction.py:125` |
| 裁剪粒度是「整轮 / 整条 / 整块」 | 不做消息内截断（除了摘要），因为消息被截一半更容易让模型困惑 | `src/myagent/agent/context.py:876` 的 `_drop_turn_size` |
| 不做多模态 token 与工具结果的重新摘要 | 工具结果进对话时就按 `max_tool_result_chars` 截断（Phase 2 的职责） | `src/myagent/agent/runner.py` |
| CLI 只压一个会话 | `session compact <key>` 是单会话命令；批量运维留给 Phase 9 | `src/myagent/cli.py:304` |

## 10. 与上游 nanobot 的对照

| 维度 | 上游 | 我们 | 取舍 |
| --- | --- | --- | --- |
| 拼装 | `ContextBuilder`（`nanobot/nanobot/agent/context.py:89`）自己持有 `MemoryStore` | `SectionedContextManager` 只消费 `ContextItem`，检索由 Loop 编排 | 上下文策略与存储解耦（`docs/design.md` §6.1） |
| 拟合预算 | `ContextGovernor.fit_to_budget`（`nanobot/nanobot/agent/context_governance.py:336`） | 同一个四步顺序，合并进 `build()` | 顺序是设计（§5），拆文件只是组织方式 |
| 预算公式 | `context_window - max_output_tokens - margin`（`nanobot/nanobot/agent/context_governance.py:693`） | 同公式，余量 1024 写成常量 | 公式一处实现（§4） |
| 摘要检查点 | `commit_summary_checkpoint`（`nanobot/nanobot/session/manager.py:323`）+ `get_history` 从 `last_archived` 重放（`nanobot/nanobot/session/manager.py:344`） | 同语义：`commit_summary` + `Session.transcript()` | 保留「只推游标、不删原文」 |
| 摘要生成 | `agent/memory.py:1103` 的 LLM 摘要 | `ModelSummarizer`（一次无工具调用 + 600 token 上限） | 失败即失败，不写半成品（§6） |
| 空闲压缩 | `agent/autocompact.py:68` 的定时器 | 不做（默认关闭） | 先要可预测的行为，再看要不要调度器 |
