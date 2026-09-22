# Phase 6 工作记录：Context Manager 重构（优先级 → 预算 → 结构修复 → 压缩）

- 日期：2026-09-22
- 状态：已完成
- 关联提交：实现提交 `feat(phase6): implement the budgeted context manager with compaction`、
  实验脚本提交 `chore(scripts): add the phase 6 context experiment harness`，
  本文档与 `docs/context-design.md`、ADR-0010、`docs/design.md` 的锚点收敛在同一次文档提交里
- 关联文档：[`docs/context-design.md`](../context-design.md)（设计）、
  [`docs/decision-records/0010-context-budget.md`](../decision-records/0010-context-budget.md)（预算与配额）、
  [`docs/context.md`](../context.md)（Phase 1 对上游上下文机制的理解）、
  [`docs/design.md`](../design.md) §2（调用链）、§3.8（Context 契约）、§6（答辩）

## 1. 阶段目标

Phase 5 结束时，`ContextManager.build()` 已经能把记忆与文档拼进 prompt，但**超预算只能报错**：
上游把「拼装」与「拟合预算」拆在 `ContextBuilder`（`nanobot/nanobot/agent/context.py:89`）与
`ContextGovernor`（`nanobot/nanobot/agent/context_governance.py:336`）里，我们合并成一个
`ContextManager`，把它的职责收敛成**四步、有固定顺序**的一次拟合：

```text
1. 按 section 配额裁剪（超配额再按优先级从大到小继续降级）
2. 删除孤儿 tool 结果        3. 补上缺失的 tool 结果    ← 修结构，不省 token
4. 校验：装不下就抛 ContextWindowExceeded（不静默截断）
```

要回答的四个问题：

| 问题 | 本阶段的答案 |
| --- | --- |
| 装不下先牺牲谁 | 一张优先级表（tools 6 → rag 5 → memory 4 → summary 3 → conversation 2），system/query 永不裁剪 |
| 每段分多少 | `input_budget = context_window - max_output_tokens - 1024`，再按 35/35/20/10 切成四档（ADR-0010） |
| 裁完的请求还合法吗 | 裁完再修结构（删孤儿 / 补缺）并以**最终数字**校验，实在装不下就明确报错 |
| 长会话怎么变短 | 显式压缩把旧轮换成一个摘要检查点，原文留在 JSONL；自动触发只裁最旧轮、不调模型 |

## 2. Baseline（Phase 5 结束时，`docs/records/phase-5-rag.md`）

| 项 | Phase 5 结束 | Phase 6 结束 |
| --- | --- | --- |
| 测试 | 591 项 | 641 项（+50；`tests/test_context.py` 重写并扩充） |
| 覆盖率 | 3550 stmts / 828 branches，100% | 4070 stmts / 978 branches，100% |
| `src/myagent/` | 53 个文件 / 8359 行 | 55 个文件 / 9660 行 |
| `agent/` | 5 个文件（context 只做拼装 + 超限报错） | 8 个文件 / 2133 行（新增 `compaction.py`、`token_budget.py`） |
| 预算 | 无：`build()` 拼完就发，超了靠 provider 报 400 | `ContextBudget` + `ContextReport` + 四步拟合 |
| 压缩 | `CompactionReport` 已在 Phase 3 预留，`compact()` 返回空报告 | 显式压缩（`session compact`）+ 自动裁剪最旧轮 |
| 开关 | `MYAGENT_MEMORY_ENABLED`（Phase 4） | + `MYAGENT_RAG_ENABLED`（Phase 5 的 `ingest`/`search` 与 Phase 6 的 section 共用） |
| 文档锚点 | 见 §8 的质量门输出 | 见 §8 的质量门输出 |

## 3. 方案设计

三个决定性的设计取舍（细节在 `docs/context-design.md`，依据在 ADR-0010）：

1. **四步顺序写死在 `build()` 里**（`src/myagent/agent/context.py:497`）：
   裁剪 → 删孤儿 → 补缺 → 校验。顺序不能反的理由是「裁剪本身会制造结构错误」，
   所以修结构必须在裁剪之后；而补缺要花 token，所以它必须记进最终数字；
2. **降级顺序由一张表决定**（`_PRIORITY_OF`，`src/myagent/agent/context.py:98`）：
   数字越小越先保留，`required` 只有 system / query
   （`_REQUIRED_SECTIONS`，`src/myagent/agent/context.py:107`）。每次降级都写进
   `ContextReport`（`src/myagent/agent/context.py:288`）而不是藏在字符串拼接里；
3. **压缩是「视图」而不是「删除」**：`session compact` 只把重放游标 `last_archived`
   往前推（`SessionStore.commit_summary`，`src/myagent/session/manager.py:92`），
   原文永远留在 JSONL（`Session.transcript()`，`src/myagent/session/base.py:45`）；
   自动触发（`SectionedContextManager.compact`，`src/myagent/agent/context.py:523`）
   只裁最旧整轮、**不调模型**，因为它在请求路径上。

## 4. 实现

| 文件 | 改动 |
| --- | --- |
| `src/myagent/agent/context.py` | 重写：七段 section（`src/myagent/agent/context.py:223`）、`ContextBudget`（`:348`）、`ContextReport`（`:288`）、四步 `build()`（`:497`）、降级循环 `_fit`（`:696`）、结构修复 `_repair`（`:729`）、`ContextWindowExceeded`（`:137`） |
| `src/myagent/agent/compaction.py` | 新增：`ModelSummarizer`（`:93`，一次无工具调用 + 600 token 上限）、`boundary_for_turns`（`:174`，边界落在轮起点）、`compact_session`（`:129`，纯决策，写入交给 `SessionStore`） |
| `src/myagent/agent/token_budget.py` | 新增：`message_tokens` / `messages_tokens` / `truncate_to_tokens` / `TokenCounter`（`src/myagent/agent/token_budget.py:39`），全部复用 `myagent.tokens.estimate_tokens`（`src/myagent/tokens.py:37`） |
| `src/myagent/agent/loop.py` | `DocumentProvider` 端口（`_recall_documents`，`:262`）、公开 `run_turn`（`:150`）供 `--show-context` 复用同一次 build |
| `src/myagent/agent/runtime.py` | `AgentRuntimeConfig.context_budget`（`:57`）；预算公式与安全余量 1024 只在这里（`:30`、`:84`） |
| `src/myagent/runtime.py` | `build_agent(retriever=…)` 注入 `SectionedContextManager(budget=…, tokens=…)`（`:88`–`:96`）、`build_rag`（`:104`） |
| `src/myagent/cli.py` | `--show-context`（`_context_transcript`，`:460`）、`myagent session compact`（`_session_compact`，`:281`） |
| `src/myagent/session/*` | `Session.summary`、`SessionStore.commit_summary`（`src/myagent/session/manager.py:92`，只前移游标） |
| `src/myagent/rag/pipeline.py` | `RagPipeline.recall()`（`DocumentProvider` 的实现）、`citation_label` |
| `src/myagent/config/settings.py` | `MYAGENT_RAG_ENABLED`（`DEFAULT_RAG_ENABLED`，`:137`） |

## 5. 实验方法

实验脚本 `scripts/context_experiment.py` 用**生产类**跑到真实路径上
（`SectionedContextManager` / `ContextBudget` / `RagPipeline` / `MemoryManager` /
`SQLiteDocumentStore` / `QdrantVectorStore`、真实切分与真实摘要提示词），
只在两处做替换以保证离线部分可复现、不依赖基础设施（与 Phase 5 的 harness 相同）：

- Qdrant 走**本地嵌入模式**（`QdrantClient(path=...)`）：同样的 payload、过滤与余弦检索，只是没有服务器；
- 开关实验的嵌入器是本地哈希嵌入器：向量必须存在（ingest 与记忆写入要存），
  但没有任何东西读回它算质量——检索**质量**是 Phase 5 的实验，不是这一个。

所有产物（维度探测写的临时 `.env`、SQLite、session 目录、Qdrant 目录）都在一个临时目录里，跑完即删。

```bash
.venv/bin/python scripts/context_experiment.py            # 三段都跑（压缩段需要 LLM_* 凭据）
.venv/bin/python scripts/context_experiment.py --offline  # 跳过需要模型的压缩段
```

三段实验分别对应 PLAN 的三条验收标准：**预算**（6.2）、**压缩**（6.4）、**开关**（6.5）。

## 6. 实验结果

运行时预算 `122880 = context_window 128000 - max_tokens 4096 - 1024`；预算实验另外用一个更小的
预算（400）把四个来源同时压到超配额，这样才量得到降级顺序。

### 6.1 预算裁剪（PLAN 6.2 / 验收标准）

四个来源塞满 → 裁剪到 `input_budget`：

| 优先级 | section | 配额 | 裁剪前 | 进入请求 | 丢弃 | 降级动作 |
| ---: | --- | ---: | ---: | ---: | ---: | --- |
| 0 | system | —（required） | 134 | 134 | 0 | — |
| 1 | query | —（required） | 26 | 26 | 0 | — |
| 2 | conversation | 140 | 400 | 100 | 300 | compacted 12 message(s) (6 turn(s)) |
| 3 | summary | 40 | 25 | 25 | 0 | — |
| 4 | memory | 80 | 172 | 60 | 112 | dropped 4 memory item(s) |
| 5 | rag | 140 | 155 | 55 | 100 | dropped 1 document chunk(s); dropped a document chunk to fit the budget; dropped a document chunk to fit the budget; dropped a document chunk to fit the budget |
| 6 | tools | 40 | 160 | 0 | 160 | dropped 5 tool description(s); dropped a tool description to fit the budget |

未裁剪时 1072 token，预算 400 token，最终 **400 token**——三个不变量都成立：
请求装得下（≤ 预算）、按优先级 6→5→4→2 依次让路（system/query 不动）、
每一次降级都在「降级动作」列里有名字。

### 6.2 压缩（PLAN 6.4 / 验收标准）

24 轮会话，事实（`128000` / `800` / `myagent_memories`）都埋在会被归档的前 18 轮里；
同一段会话发两次（原文 vs. 摘要检查点），三个探针问题都在两侧各问一次：

| 请求 | token | 说明 |
| --- | ---: | --- |
| 压缩 OFF | 937 | 全部 24 轮原文 |
| 压缩 ON | 497 | 摘要 156 token + 最近 6 轮 |

节省 440 token（**47%**，≥ 40%），归档 18 轮。摘要：

```text
- 对话共 17 轮（第 0–17 轮），主线任务：持续比较 chunk 大小与召回质量，该比较仍在进行中。
- 上下文窗口 128000，输出上限 4096。
- 文档块参数最终定为：chunk size 800、overlap 120。
- 向量库分工：记忆的向量存入 myagent_memories，文档存入 myagent_documents。
- 除上述三处实质信息外，其余各轮均为重复的「继续比较 chunk 大小和召回质量」及对应确认，无新增决策。
```

| 探针问题 | 期望出现 | 压缩 OFF 的回答 | 命中 | 压缩 ON 的回答 | 命中 |
| --- | --- | --- | :-: | --- | :-: |
| 我的上下文窗口是多少？ | 128000 | 你的上下文窗口是 128000，输出上限 4096——这是你在第 1 轮补充的背… | ✅ | 你的上下文窗口是 **128000 tokens**（输出上限 4096）。补… | ✅ |
| chunk size 定成了多少？ | 800 | 你定的是 **chunk size 800、overlap 120**（在第 5… | ✅ | 根据前面轮次的记录：**chunk size = 800，overlap = 1… | ✅ |
| 记忆的向量在哪个 collection？ | myagent_memories | `myagent_memories`（文档向量在 `myagent_docume… | ✅ | 记忆的向量存在 `myagent_memories`（文档向量存在 `myage… | ✅ |

关键事实在压缩前后都保住了：三个探针在两侧都命中。

### 6.3 开关（PLAN 6.5 / 验收标准）

同一份数据，改 `MYAGENT_RAG_ENABLED` / `MYAGENT_MEMORY_ENABLED`，走**真实**的
`RagPipeline.recall()` / `MemoryManager.recall()`：

| section | ON 条数 | OFF 条数 | ON section token | OFF section token | 关掉之后数据还在吗 |
| --- | ---: | ---: | ---: | ---: | --- |
| rag | 5 | 0 | 1478 | 0 | `myagent search` 仍有 3 条命中（共 67 块） |
| memory | 3 | 0 | 117 | 0 | `myagent memory list` 仍有 3 条记录 |

开关 ON 时的 sections：`system / query / conversation / memory / rag / tools`；
OFF 时的 sections：`system / query / conversation / tools`。
也就是说「关掉一个来源」的表现是 **section 消失**，而不是「一个有标题的空块」——
语料与存储都还在，显式命令照常工作，只有模型会看到的那一段空了。

## 7. 真实服务冒烟（PLAN 验收：四段同时出现在 transcript 里）

用真实模型（`.env` 里的 `LLM_*`）、真实记忆库与已 ingest 的两篇文档，
在一个干净会话 `cli:phase6-smoke` 上跑完整链路。

### 7.1 `--show-context`：七个 section 同时可见

```text
$ PYTHONPATH=src .venv/bin/python -m myagent chat -s cli:phase6-smoke \
      -m "上下文预算怎么算，装不下会怎样？" --show-context
--- context for cli:phase6-smoke (turn a219f20e)
budget 122880 token(s), used 2719, dropped 0
  system       priority=0 required used=123     budget=12288
  query        priority=1 required used=17      budget=12288
  conversation priority=2 optional used=558     budget=43008
  memory       priority=4 optional used=270     budget=24576
  rag          priority=5 optional used=1646    budget=43008
  tools        priority=6 optional used=105     budget=12288
  messages (13): system, user, assistant, tool, assistant, tool, assistant, tool x2, assistant, tool, assistant, user
```

`Conversation / Memory / RAG / Tools` 四段（外加 required 的 system / query）
同时出现，`dropped 0`——真实模型下这个请求远没到预算，降级只在 §6.1 的小预算实验里发生。

### 7.2 `session compact`：只推游标，不删原文

```text
$ PYTHONPATH=src .venv/bin/python -m myagent session compact cli:phase6-smoke --keep-recent 1
compacted cli:phase6-smoke: 1 turn(s) (11 message(s)) -> summary
  tokens: 1159 -> 994 (saved 165)
  replay starts at message 11; originals stay in the JSONL file
summary: （多行中文摘要：overlap=120、workspace 只读、未决问题等）
```

### 7.3 压缩后：summary section 出现在 transcript 里

```text
$ PYTHONPATH=src .venv/bin/python -m myagent chat -s cli:phase6-smoke \
      -m "文档块的 overlap 定成了多少？" --show-context
--- context for cli:phase6-smoke (turn 0c56e5f4)
budget 122880 token(s), used 3129, dropped 0
  system       priority=0 required used=123     budget=12288
  query        priority=1 required used=14      budget=12288
  conversation priority=2 optional used=601     budget=43008
  summary      priority=3 optional used=401     budget=12288
  memory       priority=4 optional used=267     budget=24576
  rag          priority=5 optional used=1618    budget=43008
  tools        priority=6 optional used=105     budget=12288
  messages (8): system, user, assistant, tool x3, assistant, user
```

压缩后 `messages` 从 13 条降到 8 条（`replay starts at message 11` 只重放尾部 +
本轮的新消息），摘要以 `summary` 段（priority 3）进入请求，模型回答仍答对 `120`。
副作用是这次冒烟把 2 条记忆与会话文件留在 `data/` 里（`data/` 被 git 忽略）。

## 8. 质量门与验收判定

```text
$ scripts/check.sh
== ruff format --check ==   86 files already formatted
== ruff check ==            All checks passed!
== mypy ==                  Success: no issues found in 55 source files
== pytest ==                641 passed（覆盖率 4070 stmts / 978 branches，100%）

$ .venv/bin/python scripts/check_doc_anchors.py
checked 1195 anchor(s) in 22 document(s)
all anchors resolve
```

整个测试套件在**没有网络**的环境里跑绿（真实的模型只在 §6.2 与 §7 的冒烟里出现，
检索质量的真实嵌入见 Phase 5）。

| PLAN Phase 6 验收项 | 结果 |
| --- | --- |
| transcript 里同时出现 Conversation / Memory / RAG / Tools 四段 | ✅ §7.1（`--show-context`，`dropped 0`） |
| 预算实验：四来源塞满 → 裁剪后 token ≤ `input_budget` 且报告记录每段降级 | ✅ §6.1（1072 → 400） |
| 压缩实验：≥ 20 轮会话压缩后 token 下降 ≥ 40% 且探针仍答对 | ✅ §6.2（937 → 497，-47%，3/3 探针两侧命中） |
| 开关实验：关 RAG → RAG section 空；关 Memory → Memory section 空 | ✅ §6.3 |
| 单测覆盖：优先级 / 配额 / 孤儿修复 / 超限报错 / 压缩报告 | ✅ `tests/test_context.py`（含 PLAN 点名的三个用例） |
| `scripts/check.sh` 全绿 | ✅ 641 项、100% 覆盖 |

PLAN 点名的三个验收用例都落在 `tests/test_context.py`：
`test_priority_order`（6.1）、`test_budget_clipping`（6.2）、`test_orphan_tool_repair`（6.3）。

## 9. 过程中发现并修掉的真实问题

### 9.1 探针命中判定被千分位骗过（实验脚本的真实缺陷）

压缩实验的第一版用 `term in answer` 做命中判定。第一次跑，探针
「我的上下文窗口是多少？」在压缩 ON 侧报 ❌——但模型回答里明明写着
`128,000`。原因是模型把 `128000` 写成了带千分位的 `128,000`，纯子串匹配就漏了。
这不是信息丢失，是**度量的假阴性**：把 `ProbeRow` 的命中判定改成先归一化
（`_normalise`，`scripts/context_experiment.py:275` 起：小写 + 抹掉数字之间的
`,`/空格/`_`），两侧就都命中了。修完之后三个探针在压缩前后各 3/3 命中，
验收标准「关键信息未丢」才是被可信地测出来的。

## 10. 结论与遗留问题

结论（证据见 §6～§8）：

1. **四步拟合可以只用一个文件、一套不变量解释**：裁剪 → 删孤儿 → 补缺 → 校验
   （`src/myagent/agent/context.py:497`），顺序是设计而不是实现细节——
   结构合法性优先于省 token（§6.1 的降级顺序、§6.3 的 section 消失都从这条推出来）；
2. **降级是可观测的，不是静默的**：`_PRIORITY_OF`（`src/myagent/agent/context.py:98`）
   一张表定顺序，每次降级写进 `ContextReport`（`:288`），`--show-context` 与日志读到的是同一份；
3. **预算公式与配额一处实现**：`input_budget = context_window - max_output_tokens - 1024`
   （`src/myagent/agent/runtime.py:84`，与上游 `context_governance.py:693` 同形），
   四档 35/35/20/10（ADR-0010）——`build()` 里没有任何常量，Phase 8 改比例只需改常量；
4. **压缩保住了关键信息且省了 47%**：24 轮压缩到摘要 + 最近 6 轮，请求 937 → 497 token，
   三个埋在旧轮里的事实探针在两侧全部命中（§6.2）；摘要是「视图」不是「删除」，
   原文留在 JSONL（`src/myagent/session/base.py:45`）；
5. **开关是类型强制的**：`agent/` 不 import `memory/` / `rag/`，只认两个 Protocol
   （`src/myagent/agent/context.py:180`、`:204`），所以关掉一个来源就是那段
   section 消失，语料与存储照常（§6.3）——`tests/test_contracts.py` 的 AST 检查钉住了这条边界。

遗留问题（明确不做或留给后续阶段）：

- **空闲压缩（autocompact）未实现**：PLAN 6.4 写「默认关闭」，没有定时器/后台任务，
  压缩只由 CLI 或配额触发；
- **token 仍是估算**：`BaseModel.count_tokens()` 返回 `None`，用 `tokens.py` 的
  4 字符≈1 token；契约已留口，提供方实现后自动接管（`src/myagent/agent/token_budget.py:39`）；
- **摘要上限是常量**：`DEFAULT_SUMMARY_TOKENS = 600` 不随窗口缩放（`src/myagent/agent/compaction.py:61`）；
- **四档比例是起点**：35/35/20/10 由 ADR-0010 记录，留待 Phase 8 用更大样本复算；
- **配额是软上限**：每段都没超配额并不保证总量装得下——两者不一致时报错，不静默截断。
