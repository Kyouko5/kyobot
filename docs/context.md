# Context 系统

> 基线：nanobot v0.3.5 / `2fb165939`，本地只读参照在 `nanobot/`。
> **路径约定**：`agent/context.py:89` 指上游包内的 `nanobot/nanobot/agent/context.py` 第 89 行。
> 核心文件：`agent/context.py`（组装）、
> `agent/context_governance.py`（预算与压缩）、`agent/autocompact.py`（空闲压缩）、
> `session/summary.py`（摘要检查点）。
>
> 本文只讲**上游**的机制。我们自己的实现（七段 section、优先级、四档预算、四步拟合与
> 摘要检查点，Phase 6）见 [`docs/context-design.md`](./context-design.md)；
> 预算与配额的决策依据见 [`docs/decision-records/0010-context-budget.md`](./decision-records/0010-context-budget.md)。

## 0. 心智模型

```text
Context = System Prompt  +  Transcript（历史 + 本轮输入）
          │                 │
          │                 └── 原始转录永不修改；发给模型的是「按预算拟合过的副本」
          └── identity / AGENTS.md / SOUL.md / USER.md / tool contract /
              workspace / long-term memory / skills / 会话摘要
```

三个必须分清的层次：

| 层次 | 是什么 | 存在哪 | 谁负责 |
| --- | --- | --- | --- |
| 原始转录（raw transcript） | 这个 session 的全部消息（含已归档部分） | `Session.messages`（磁盘 JSONL） | `session/manager.py` |
| 模型请求（model request） | 真正发给 provider 的 messages | 内存 | `ContextGovernor` |
| 系统提示（system prompt） | 每轮重新拼装的「身份 + 长期上下文」 | 内存 | `ContextBuilder` |

**这条「原始转录 ≠ 模型请求」的区分是上游 Context 设计里最重要的一点**：
压缩只改变模型看到的内容，不改变落盘的历史，所以历史永远可重放、可追溯。

---

## 1. System Prompt 的组成

```python
# agent/context.py:101
def build_system_prompt(
    self, *, channel=None, session_summary=None, workspace=None, include_memory=True,
) -> str:
    """Build the system prompt from identity, bootstrap files, memory, and skills."""
    root = workspace or self.workspace
    parts = [self._get_identity(channel=channel, workspace=root)]
    bootstrap = self._load_bootstrap_files(root)
    if bootstrap:
        parts.append(bootstrap)
    parts.append(render_template("agent/tool_contract.md"))
    ...
    if include_memory:
        memory = self.memory.read_memory()
        if memory and not self._is_template_content(memory, "memory/MEMORY.md"):
            parts.append(f"# Memory\n\n## Long-term Memory\n{memory}")
    ...
    if session_summary and session_summary["text"] != "(nothing)":
        parts.append("[Archived Context Summary]\n\n...")
    return "\n\n---\n\n".join(parts)
```

拼装顺序（`agent/context.py:101-153`）：

| # | 段落 | 来源 | 备注 |
| --- | --- | --- | --- |
| 1 | Identity | `templates/agent/identity.md` | 含 workspace 路径、运行时、平台策略 |
| 2 | Bootstrap 文件 | `AGENTS.md`（项目）、`SOUL.md`、`USER.md`（agent 工作区） | 见 1.1 |
| 3 | Tool contract | `templates/agent/tool_contract.md` | 工具使用约定 |
| 4 | Current Project | 当 `workspace != 默认工作区` 时追加 | 多项目隔离 |
| 5 | Long-term Memory | `memory/MEMORY.md` | 见 `docs/memory.md` |
| 6 | Active Skills | `SkillsLoader` | 常驻技能全文 |
| 7 | Skills 摘要 | `templates/agent/skills_section.md` | 其它技能只给摘要 |
| 8 | Archived Context Summary | `session.metadata["_last_summary"]` | 压缩后的历史摘要 |

### 1.1 Bootstrap 文件与「模板检测」

```python
# agent/context.py:92
BOOTSTRAP_FILES = ["AGENTS.md", "SOUL.md", "USER.md"]
_SKIPPABLE_DEFAULTS = {"AGENTS.md", "USER.md"}

# agent/context.py:194  _load_bootstrap_files
sources = [("AGENTS.md", project_root), ("SOUL.md", self.workspace), ("USER.md", self.workspace)]
```

两个细节值得学：

1. **作用域不同**：`AGENTS.md` 从「当前项目目录」读，`SOUL.md` / `USER.md` 从「agent 工作区」读——
   项目规则与 agent 人格是两件事。
2. **模板内容会被跳过**（`_is_template_content`，`agent/context.py:224`）：如果文件内容与模板一字不差，
   说明用户没改过，就不浪费 token 塞进 prompt。这是「默认配置零成本」的典型实现。

---

## 2. Transcript 的组装

### 2.1 输入结构

```python
# agent/context.py:73
class TranscriptInput:
    """Raw turn inputs from which ``ContextBuilder`` assembles a transcript."""
    history: list[dict[str, Any]]
    current_message: str | None
    media: Sequence[str] | None = None
    current_role: str = "user"
    session_summary: SessionSummary | None = None
```

### 2.2 组装函数

```python
# agent/context.py:276
def build_transcript(self, transcript, *, channel=None, workspace=None, include_memory=True):
    messages = [
        {"role": "system", "content": self.build_system_prompt(...)},
        *transcript.history,
    ]
    if transcript.current_message is None:
        return messages
    messages.append(self.build_current_message(...))   # 新的一轮单独成条
    return messages
```

| 函数 | 位置 | 用途 |
| --- | --- | --- |
| `build_transcript` | `agent/context.py:276` | Loop 调用的主入口：system prompt + history + 本轮消息 |
| `build_current_message` | `agent/context.py:310` | 只构造本轮消息，**不与历史合并**（保留「新 turn 边界」） |
| `build_messages` | `agent/context.py:231` | 兼容旧调用：会合并相邻的同角色消息 |
| `build_user_content` | `agent/context.py:334` | 文本 + 图片（base64 data URL）多模态内容 |

> 为什么要有 `build_current_message` 这种「只构造不合并」的版本？因为上下文治理需要精确知道
> 「哪一条是本轮新输入」（H/Δ 边界），合并进历史后就分不清了。

### 2.3 Runtime Context Blocks

除了会话消息，还有一类「随请求变化的上下文」：当前时间、goal 状态、显式技能等。
它们通过 `append_runtime_context` 追加到本轮用户消息里，并在消息 `_meta` 中留下标记
（`agent/context.py:310-333`），这样落盘后仍能区分「用户原话」与「系统注入」。

---

## 3. 预算与压缩（Context Governance）

### 3.1 输入预算的算法

```python
# agent/context_governance.py:693
def input_budget(config: ContextGovernanceConfig) -> int:
    ...
    max_output = config.max_tokens if isinstance(config.max_tokens, int) else provider_max_tokens
    budget = config.context_window_tokens - max_output - SNIP_SAFETY_BUFFER   # 安全余量 1024
    return budget if budget > 0 else 0
```

`input_budget = 上下文窗口 − 最大输出 − 1024`。安全余量是为了兜住 tokenizer 估算误差
（`agent/memory.py:1075` 里也有一段同样的 `_SAFETY_BUFFER = 1024`）。

### 3.2 什么时候触发

```python
# agent/context_governance.py:384
def request_pressure(self, config, messages, usage, *, usage_matches_messages,
                     tool_definitions, request_context_tokens=None):
    """Return the authoritative measurement when a request is pressured."""
```

压力判定优先使用**真实数据**，估算只作为兜底：

1. `request_context_tokens`（provider 原生状态 + 待发消息）→ 最准；
2. 上一次 provider 返回的 `usage.context_tokens`（且必须与当前 messages 一致）→ 次准；
3. `estimate_prompt_tokens_chain(...)`（tokenizer 估算）→ 兜底。

### 3.3 压缩怎么发生

```python
# agent/context_governance.py:336
def fit_to_budget(self, config, messages, *, tool_definitions):
    """Fit a model-facing copy while keeping the source transcript intact."""
    updated = self.snip_history(config, messages, tool_definitions=tool_definitions, force=True)
    updated = self.drop_orphan_tool_results(updated)
    updated = self.backfill_missing_tool_results(updated)
    return self.ensure_request_fits(config, updated, tool_definitions=tool_definitions)
```

四步是「先保证结构合法，再保证能装下」：

| 步骤 | 位置 | 做什么 |
| --- | --- | --- |
| `snip_history` | `agent/context_governance.py:951` | 从中间裁剪历史，保留开头（system + 最早的 user 锚点）与结尾 |
| `drop_orphan_tool_results` | `agent/context_governance.py:855` | 删掉「没有对应 assistant tool_call」的 tool 消息（provider 会报错） |
| `backfill_missing_tool_results` | `agent/context_governance.py:886` | 给「有 tool_call 但没有结果」的位置补占位文本（`BACKFILL_CONTENT`，`:70`） |
| `ensure_request_fits` | `agent/context_governance.py:358` | 仍然超预算就抛 `ContextWindowExceededError`（`:76`），交给上层做 LLM 摘要式压缩 |

**为什么不是「删掉最老的消息」这么简单**？因为工具调用是有结构的：孤立的 tool 结果、
没有结果回报的 tool_call 都会被 provider 拒绝，所以裁剪必须保持消息序列的「合法性」。

### 3.4 工具结果的单独预算

```python
# agent/context_governance.py:709
def normalize_tool_result(self, config, tool_call_id, tool_name, result) -> str:
    ...
```

单条工具结果按 `max_tool_result_chars` 处理：超长内容被截断或用占位符替代
（`TOOL_RESULT_OFFLOAD_EXEMPT_TOOLS = {"read_file"}` 允许读文件例外，`:69`）。
Runner 在把结果写成 tool 消息前调用它（`agent/runner.py:533`）。

### 3.5 LLM 摘要式压缩（把历史换成检查点）

当 `snip_history` 也压不进预算、或空闲会话需要主动压缩时，走「总结」路线。
发起方是 `_compact_request_history`（`agent/context_governance.py:521`），它把
「已确认的输入前缀 H」换成摘要，同时保留尚未发送的增量 Δ：

```python
# agent/context_governance.py:521（节选）
summary = await compaction.consolidate_history(      # → Consolidator.summarize_transcript
    deepcopy(consolidation_prefix), compaction.active_summary,
)
prepared = self.prepare_messages_for_model(state.config, [
    *self._summary_transcript(compaction, summary),   # 摘要替换旧前缀
    {"role": "user", "content": SUMMARY_CONTINUATION_TEXT},
    *delta_messages,                                  # 未发送的增量原样保留
])
compaction.summary_checkpoint = SessionSummaryCheckpoint(
    summary=summary, transcript_boundary=compaction.raw_accepted_boundary,
)
```

这个 `summary_checkpoint` 会随 `AgentRunResult` 返回给 Loop，在 `save` 阶段落到会话上
（`agent/loop.py:2014` → `_save_turn`，`agent/loop.py:2140`）。

真正调用 LLM 生成摘要的是 `Consolidator`（`agent/memory.py:1072`）：

```python
# agent/memory.py:1103
async def summarize_transcript(self, accepted_messages, previous_summary, *, runtime,
                               session_key, tools, provider_state=None) -> str | None:
    """Summarize the exact transcript prefix already accepted by the model."""
```

摘要写回会话的方式是插入一个**隐藏的继续标记**，并把 `last_archived` 前移：

```python
# session/manager.py:323
def commit_summary_checkpoint(self, summary, *, insert_at=None, last_active=None):
    """Replace replay before a hidden boundary while preserving the transcript."""
    boundary = len(self.messages) if insert_at is None else insert_at
    self.messages.insert(boundary, {
        "role": "user", "content": SUMMARY_CONTINUATION_TEXT,
        HIDDEN_HISTORY_META: True, "timestamp": ...,
    })
    self.metadata["_last_summary"] = {"text": summary, "last_active": ...}
    self.last_archived = boundary
```

而重放时 `get_history`（`session/manager.py:344`）从 `last_archived` 开始取，
并跳过 `_command` 之类不参与重放的消息——**旧消息还在文件里，只是不再进 prompt**。

### 3.6 空闲压缩（AutoCompact）

```python
# agent/autocompact.py:68
def check_expired(self, schedule_background, resolve_runtime, active_session_keys=()):
    """Schedule archival for idle sessions, skipping those with in-flight agent tasks."""
```

触发链路：`AgentLoop.run()` 的 1 秒超时 → `_check_expired_sessions_if_due`（`agent/loop.py:1249`，
扫描间隔由 `idle_compact_check_interval_seconds` 控制）→ `AutoCompact.check_expired`
（`agent/autocompact.py:68`）→ `Consolidator.compact_idle_session`（`agent/memory.py:1222`）。

被跳过的会话：内部会话（`dream:` 前缀）、正在归档的、以及**当前有活跃 turn 的**
（`active_session_keys` 传的就是 pending queue 的 key 集合）。

---

## 4. 迁移到 myagent

| 上游机制 | 迁移计划 | 理由 |
| --- | --- | --- |
| System prompt 分层拼装 | **保留并显式化** | Phase 6 要把它变成「优先级 + 预算」的可配置流水线 |
| Bootstrap 文件（AGENTS/SOUL/USER） | 保留 | 与 Codex/Claude 的约定一致，用户心智成本低 |
| 模板内容跳过 | 保留 | 零成本默认值 |
| `TranscriptInput` + `build_current_message` | **保留** | 「新 turn 边界」是后续注入/压缩正确性的基础 |
| 「原始转录 ≠ 模型请求」 | **保留（核心）** | 记忆与 RAG 都会往上下文里加料，没有这条区分就无法解释「删了什么」 |
| `fit_to_budget` 的四步结构修复 | 保留思想 | 结构合法性 > 省 token，顺序不能反 |
| token 估算三级（provider usage > 原生状态 > tokenizer） | 保留 | 估算永远不准，优先用真实值 |
| `snip_history` 的裁剪策略 | Phase 6 重新设计 | 上游针对多频道做了很多兼容处理，我们只需要「保留 system + 最近 N 轮 + 首轮锚点」 |
| `ContextWindowExceededError` | 保留 | 明确失败优于静默截断 |
| 空闲压缩（AutoCompact） | 简化：显式 `compact()` + 可选定时 | 定时器是运维复杂度，Phase 6 评估收益后再加 |
| 摘要检查点插隐藏标记 | 保留 | 与我们的「分层 Memory」天然契合（见 `docs/memory.md`） |

## 5. 速查索引

| 主题 | 位置 |
| --- | --- |
| `ContextBuilder` | `agent/context.py:89` |
| 系统提示拼装 | `agent/context.py:101` |
| Bootstrap 文件加载与模板检测 | `agent/context.py:194`、`224` |
| Transcript 组装 | `agent/context.py:276`、`310`、`334` |
| 输入预算公式 | `agent/context_governance.py:693` |
| 压力判定 | `agent/context_governance.py:384` |
| 四步拟合 | `agent/context_governance.py:336`、`951`、`855`、`886`、`358` |
| 工具结果归一化 | `agent/context_governance.py:709` |
| 请求准备与压缩事件 | `agent/context_governance.py:593` |
| LLM 摘要 | `agent/memory.py:1103` |
| 摘要检查点写入 | `session/manager.py:323` |
| 重放起点 | `session/manager.py:344` |
| 空闲压缩 | `agent/autocompact.py:68`、`agent/memory.py:1222` |
