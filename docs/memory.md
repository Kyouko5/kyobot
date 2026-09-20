# Memory 系统

> 基线：nanobot v0.3.5 / `2fb165939`，本地只读参照在 `nanobot/`。
> **路径约定**：`agent/memory.py:58` 指上游包内的 `nanobot/nanobot/agent/memory.py` 第 58 行。
> 核心文件：`agent/memory.py`（存储 / 归档 / 整合）、
> `session/manager.py`（会话历史）、`session/summary.py`（摘要检查点）。
>
> 本文只讲**上游**的机制。我们自己的实现（四层记忆、写入策略、检索与巩固，
> Phase 4）见 [`docs/memory-design.md`](./memory-design.md)；
> 「上游 → 本项目」的落地对照见本文 §5.1。

## 0. 心智模型：Session 与 Memory 是两件事

| 维度 | Session（短期） | Memory（长期） |
| --- | --- | --- |
| 回答的问题 | 「这次对话进行到哪了」 | 「我（agent）知道些什么」 |
| 数据形态 | 逐条消息的转录（JSONL） | 人类可读的 markdown + 追加式日记（JSONL） |
| 存储位置 | `~/.nanobot/sessions/<workspace-id>/<key>.jsonl`（**在 workspace 之外**） | `<workspace>/memory/MEMORY.md`、`<workspace>/memory/history.jsonl`、`<workspace>/SOUL.md`、`<workspace>/USER.md` |
| 写入时机 | 每轮 `save` 阶段 | 归档（archive）、整合（Dream） |
| 生命周期 | 可删、可恢复、有 TTL | 长期沉淀，进 Git 版本管理 |
| 谁读它 | `session.get_history()` → transcript | `ContextBuilder.build_system_prompt()` |

一句话：**Session 是「可重放的过程」，Memory 是「被提炼的结论」。**
两者都进 Context，但进入的位置完全不同（见第 4 节）。

```text
                    一轮对话结束
                         │
              ┌──────────┴──────────┐
              ▼                     ▼
      写回 Session（原文转录）   归档 → 摘要检查点
              │                     │
              │                     └── 写 history.jsonl（可追溯日记）
              │
              ▼
        下一轮重放（last_archived 之后）
                         │
                    Dream（每 2 小时）
                         │
                         ▼
        SOUL.md / USER.md / memory/MEMORY.md（长期记忆，进系统提示）
```

---

## 1. 存储层：MemoryStore

```python
# agent/memory.py:58
class MemoryStore:
    """Pure file I/O for memory files: MEMORY.md, history.jsonl, SOUL.md, USER.md."""

    def __init__(self, workspace: Path, max_history_entries: int = _DEFAULT_MAX_HISTORY):
        self.memory_dir = ensure_dir(workspace / "memory")
        self.memory_file = self.memory_dir / "MEMORY.md"
        self.history_file = self.memory_dir / "history.jsonl"
        self.soul_file = workspace / "SOUL.md"
        self.user_file = workspace / "USER.md"
        self._cursor_file = self.memory_dir / ".cursor"
        self._dream_cursor_file = self.memory_dir / ".dream_cursor"
```

### 1.1 四个文件各自的角色

| 文件 | 内容 | 谁写 | 谁读 |
| --- | --- | --- | --- |
| `SOUL.md` | agent 的人格 / 价值观 | Dream（或用户手改） | system prompt（bootstrap） |
| `USER.md` | 用户的稳定偏好与背景 | Dream（或用户手改） | system prompt（bootstrap） |
| `memory/MEMORY.md` | 长期事实、结论、项目知识 | Dream（或用户手改） | system prompt 的 `# Memory` |
| `memory/history.jsonl` | 追加式日记：每轮归档一条 | `MemoryArchiver` | Dream（按 cursor 增量消费） |

另有三个「指针/状态」文件：`.cursor`（日记写到第几条）、`.dream_cursor`（Dream 处理到第几条）、
以及 `GitStore`（`agent/memory.py:94`）对 `SOUL.md`、`USER.md`、`MEMORY.md`、`.dream_cursor` 的版本化。

### 1.2 history.jsonl 的记录结构

```python
# agent/memory.py:282  append_history（节选）
with self._append_lock:
    cursor = self._next_cursor()
    record = {"cursor": cursor, "timestamp": ts, "content": content}
    if session_key:
        record["session_key"] = session_key
    with open(self.history_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    self._cursor_file.write_text(str(cursor), encoding="utf-8")
```

设计要点：

1. **自增 cursor** 是消费位点，Dream 靠它做增量处理（`read_unprocessed_history`，`agent/memory.py:399`）。
2. **cursor 分配与写入在同一把锁内**（`agent/memory.py:306` 注释写得很清楚：否则并发写会重复分配 cursor）。
3. **两道长度防线**：单个条目默认硬上限 `_HISTORY_ENTRY_HARD_CAP = 64_000`（`agent/memory.py:751`），
   并且先过 `strip_think` 清掉模板泄漏（`_normalize_history_entry`，`agent/memory.py:259`）。
4. **写失败不污染**：若清理后为空但原文非空，写入空串而不是回落原文——避免把污染再喂给 Dream。

`read_memory()` / `write_memory()`（`agent/memory.py:229`、`232`）就是普通的文件读写，
外加 `get_memory_context()`（`agent/memory.py:253`）给上下文用的 `## Long-term Memory` 片段。

---

## 2. 归档：把 session 转录压成摘要 + 日记

### 2.1 谁触发

| 触发源 | 入口 | 场景 |
| --- | --- | --- |
| 一轮内请求超预算 | `ContextGovernor._compact_request_history` → `Consolidator.summarize_transcript` | 对话太长，必须压缩才能发请求（`agent/context_governance.py:521`） |
| 空闲 TTL | `AutoCompact.check_expired` → `Consolidator.compact_idle_session` | 会话闲置（`idleCompactAfterMinutes`，默认 15 分钟） |
| Dream 定时任务 | gateway 的 system job（`enabled` + `intervalH`，默认 2 小时，`config/schema.py:53`） | 把日记提炼进长期记忆 |
| 手动 | `/dream`、`/compact` 命令（`command/builtin.py:451`） | 调试与显式控制 |

### 2.2 归档一次调用链

```python
# agent/memory.py:996
async def archive_session(self, session, *, archive_end, runtime, input_token_budget):
    """Archive a captured session prefix without mutating the session."""
    messages = [m for m in session.messages[session.last_archived:archive_end]
                if not m.get("_command") and not is_summary_checkpoint(m)]
    if not messages:
        return None
    ...
    return await self.archive(messages, runtime=runtime, session_key=session.key,
                              history=history_messages, request_tools=tools,
                              previous_summary=previous_summary, ...)
```

`MemoryArchiver.archive`（`agent/memory.py:826`）做三件事：

1. 用 `agent/consolidator_archive.md` 模板拼一个「请总结这段对话」的提示（带 `previous_summary`，
   实现**滚动摘要**：新摘要建立在旧摘要之上）；
2. 走一次模型调用拿摘要（若预算不足或调用失败，退化为 `_raw_checkpoint` 原始转储，
   `agent/memory.py:750` `_RAW_ARCHIVE_MAX_CHARS = 16_000`）；
3. 把结果写到 `history.jsonl`（作为可追溯日记）。

### 2.3 摘要如何影响后续重放

```python
# session/manager.py:323
def commit_summary_checkpoint(self, summary, *, insert_at=None, last_active=None):
    """Replace replay before a hidden boundary while preserving the transcript."""
    boundary = len(self.messages) if insert_at is None else insert_at
    self.messages.insert(boundary, {
        "role": "user",
        "content": SUMMARY_CONTINUATION_TEXT,
        HIDDEN_HISTORY_META: True,
        "timestamp": datetime.now().isoformat(),
    })
    self.metadata["_last_summary"] = {"text": summary, "last_active": ...}
    self.last_archived = boundary
```

```python
# session/manager.py:344
def get_history(self, max_messages=0, *, max_tokens=0, extend_to_user=False,
                include_runtime_context=True) -> list[dict[str, Any]]:
    """Return recent replayable messages for LLM input."""
    replayable = self.messages[self.last_archived:]   # ← 从归档边界之后开始
    ...
```

于是同一份文件里同时存在「完整转录」和「压缩后的可见历史」：

```text
Session.messages（磁盘全量）
├── [0 .. last_archived)        ← 已归档：不再重放，摘要文本存在 metadata["_last_summary"]
└── [last_archived .. end)      ← 重放区间（get_history 的返回）
        ↑ 边界处插入了隐藏的 SUMMARY_CONTINUATION_TEXT 标记
```

**为什么保留原文？** 三个理由：可追溯（审计 / 调试）、可重建（换模型/换摘要策略可重新总结）、
以及「摘要做错了还能回头」。代价是文件会变大，因此 `compact_history`（`agent/memory.py:403`）负责裁剪超量条目。

---

## 3. Dream：把日记变成长期记忆

Dream 是上游最有意思的机制：**定时让 agent 读自己的日记，然后重写自己的长期记忆文件**。

### 3.1 触发与工作目录

```python
# agent/memory.py:699
def dream_session_key() -> str:
    """Return a unique session key for a Dream run, e.g. ``dream:20260528-100000``."""
    return f"dream:{datetime.now():%Y%m%d-%H%M%S}"
```

```python
# config/schema.py:53
class DreamConfig(Base):
    """Dream memory consolidation configuration."""
    enabled: bool = True            # Register the periodic Dream consolidation job on startup
    interval_h: int = Field(default=2, ge=1)   # Every 2 hours by default
```

运行方式（`cli/gateway_runtime.py:561` 附近的 system job 分支）：

```text
if job.name == "dream":           # Dream is an internal job — run directly, not through the agent loop
    build_dream_prompt()          # 读未处理的 history.jsonl 条目
    key = dream_session_key()     # dream:YYYYMMDD-HHMMSS
    run agent with dream_runtime + store.build_dream_tools()
    diff = dream_content_diff()   # SOUL.md / USER.md / MEMORY.md 的改动
    git commit（commit message = dream: periodic memory consolidation）
    set_last_dream_cursor(last_cursor)   # 只有成功才前移游标
```

### 3.2 输入与输出

```python
# agent/memory.py:543
def build_dream_prompt(self, *, max_entries: int = 20) -> tuple[str, int] | None:
    """Build the Dream prompt with unprocessed history context.
    Returns ``(prompt, last_cursor)`` or ``None`` if nothing to process.
    """
    last_cursor = self.get_last_dream_cursor()
    entries = self.read_unprocessed_history(since_cursor=last_cursor)
    if not entries:
        return None
```

| 项 | 内容 |
| --- | --- |
| 输入 | 最多 20 条未处理日记（每条截断到 1000 字符） + 由 system prompt 提供的当前 SOUL/USER/MEMORY |
| 工具 | `build_dream_tools()`（`agent/memory.py:575`）——只给 Dream 用的「改写记忆文件」工具集 |
| 输出 | 对 `SOUL.md` / `USER.md` / `memory/MEMORY.md` 的真实文件改动 |
| 落盘 | `GitStore` 提交（可用 `/dream-log`、`/dream-restore` 查看与回滚） |
| 成功判定 | `dream_run_completed()`（`agent/memory.py:619`）：`stop_reason == "completed"` 才前移游标 |

**这个设计的好处**：长期记忆不是「代码里写死的规则」，而是一次可回滚的代码提交——
记忆的写入有 diff、有历史、有回滚点，正好回答「如何避免 Memory 污染」这个问题。

---

## 4. Memory 与 Context 的关系

两条路径，互不干扰：

```python
# 路径 1：长期记忆 → system prompt
# agent/context.py:127（build_system_prompt 内）
if include_memory:
    memory = self.memory.read_memory()
    if memory and not self._is_template_content(memory, "memory/MEMORY.md"):
        parts.append(f"# Memory\n\n## Long-term Memory\n{memory}")

# 路径 2：会话历史 → transcript
# agent/loop.py:1865（build 阶段）
ctx.history = session.get_history(extend_to_user=is_subagent)
```

| 进入点 | 来源 | 位置 | 变化频率 |
| --- | --- | --- | --- |
| system prompt 的 `# Memory` 段 | `memory/MEMORY.md` | `agent/context.py:127` | 低（Dream 改写时） |
| system prompt 的 bootstrap 段 | `SOUL.md` / `USER.md` / `AGENTS.md` | `agent/context.py:194` | 低 |
| system prompt 的 `[Archived Context Summary]` | `session.metadata["_last_summary"]` | `agent/context.py:145` | 中（每次归档） |
| transcript 的历史部分 | `session.get_history()` | `session/manager.py:344` | 每轮 |
| transcript 的本轮输入 | `TranscriptInput.current_message` | `agent/context.py:310` | 每轮 |

三个推论：

1. `include_memory=False`（`ephemeral` 会话或 `SessionPolicy.persist=False`）能让「记忆全关」，
   这正好是 Phase 8 做 `Memory ON / OFF` 实验的开关。
2. 记忆「污染」的风险点是 `MEMORY.md` 与 `SOUL.md`：它们总是无条件进入 system prompt，
   因此 Dream 的质量直接决定后续所有对话的上下文质量。
3. Session 与 Memory 的边界就是「原文 vs 提炼」：**归档把原文变成摘要与日记，Dream 把日记变成结论。**

---

## 5. 迁移到 myagent

| 上游机制 | 迁移计划 | 理由 |
| --- | --- | --- |
| Session（JSONL 转录）/ Memory（文件 + 日记）分离 | **保留** | 这是 Phase 4 分层记忆的地基 |
| `history.jsonl` + 自增 cursor | 保留 | 追加写 + 游标消费简单、可审计、天然支持增量 |
| 摘要检查点（原文保留 + 隐藏边界） | 保留 | 让「压缩」可回溯，是避免信息永久丢失的关键 |
| `GitStore` 版本化记忆文件 | 保留（Phase 4 评估是否用 git 还是 SQLite 快照） | 记忆写错能回滚；ADR-0003 已选 SQLite 存储，这里需要一次对比实验 |
| Dream 定时改写记忆 | **改造为显式 Memory Pipeline** | 上游把「归档」与「整合」绑在定时任务里；我们要拆成可测试的 `MemoryWriter` + `MemoryConsolidator` |
| 单一 `MEMORY.md` | **拆成 Working / Episodic / Semantic**（Phase 4） | 上游只有「长期记忆」一层，无法解释「什么该记住、记多久」 |
| 向量检索 | 上游没有，Phase 4/5 新增 | 我们的 Memory Retriever + RAG 是增量能力 |
| 记忆工具集（dream tools） | 保留思想 | 「让模型自己写记忆」比「规则抽取」更通用，但要加写入校验 |

### 5.1 Phase 4 实际落地（对照上表）

| 上游机制 | 实际做法 | 位置 |
| --- | --- | --- |
| Session / Memory 分离 | 保留：Session 仍是 JSONL，Memory 落 SQLite（同库不同表） | `src/myagent/session/manager.py:115`、`src/myagent/memory/sqlite_store.py:49` |
| `history.jsonl` + 自增 cursor | 保留会话侧；记忆侧换成 `memories.consolidated_at IS NULL` 当增量游标 | `src/myagent/memory/sqlite_store.py:199` |
| 摘要检查点（`last_archived`） | **未在 Phase 4 使用**：记忆只读会话历史，不改会话；留给 Phase 6 的预算裁剪 | `src/myagent/session/base.py:38` |
| `GitStore` 版本化记忆 | 不采用：记录进 SQLite，`source` / `metadata` / `consolidated_from` 提供可审计性 | `src/myagent/memory/types.py:42`、`src/myagent/memory/consolidator.py:166` |
| Dream 定时改写 | 改为显式的 `Consolidator`（`myagent memory consolidate`），**只有成功才前移游标** | `src/myagent/memory/consolidator.py:92` |
| 单一 `MEMORY.md` | 拆成 Working / Episodic / Semantic 三层 + Retriever | `src/myagent/memory/manager.py:58` |
| 向量检索（上游没有） | 新增：SQLite 存记录、Qdrant 存向量、`memory_id` 关联 | `src/myagent/memory/vector_index.py:87` |
| 记忆工具集（dream tools） | 保留「让模型自己写」但加校验：候选过 JSON schema + 写入策略 | `src/myagent/memory/extractor.py:150`、`:184` |

## 6. 速查索引

| 主题 | 位置 |
| --- | --- |
| `MemoryStore` 与文件布局 | `agent/memory.py:58`、`72` |
| Git 版本化 | `agent/memory.py:94` |
| 日记追加与 cursor | `agent/memory.py:282`、`399` |
| 归档调用链 | `agent/memory.py:996`、`826` |
| Dream 提示与游标 | `agent/memory.py:543`、`704`、`619` |
| `Consolidator` | `agent/memory.py:1072`、`1103`、`1222` |
| Session 结构 | `session/manager.py:276` |
| 重放与摘要边界 | `session/manager.py:344`、`323` |
| 摘要元数据 | `session/summary.py:23`、`28` |
| 会话存储位置约束 | `session/manager.py:548`（必须位于 workspace 之外） |
| 记忆进入上下文 | `agent/context.py:127`、`194`、`145` |
