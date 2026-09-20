# Agent Loop 与 Agent Runner

> 基线：nanobot v0.3.5 / `2fb165939`，本地只读参照在 `nanobot/`。
> **路径约定**：`agent/loop.py:196` 指上游包内的 `nanobot/nanobot/agent/loop.py` 第 196 行；
> 只写文件名（如 `agent/loop.py:1261`）的引用属于同一约定。
> 阅读顺序建议：先看本文的「心智模型」，再按需跳到具体小节。

## 0. 心智模型

```text
        AgentLoop（产品语义：一轮对话）                AgentRunner（执行语义：一次模型-工具循环）
┌────────────────────────────────────────┐   ┌──────────────────────────────────────────┐
│ 收消息 / 会话解析 / 上下文组装 / 落盘 / 出站 │ → │ 调 LLM / 执行工具 / 回灌结果 / 迭代 / 终止判定 │
└────────────────────────────────────────┘   └──────────────────────────────────────────┘
         知道 channel、session、workspace              只知道 messages、tools、provider
```

一句话：**Loop 负责「和谁、用哪份上下文、结果写到哪」，Runner 负责「怎么和模型来回几轮」。**

上游自己也是这么描述的（`nanobot/docs/architecture.md`：「Loop owns the channel-facing turn，
Runner owns the model-facing loop」），代码结构上这个边界是**单向依赖**：

```text
agent/loop.py    →  import AgentRunner, AgentRunSpec, AgentRunResult   (agent/loop.py:38)
agent/runner.py  →  只 import context / hook / tools / providers / utils（没有任何 loop 相关导入）
```

这条单向依赖是后面 Phase 3「解耦 AgentLoop」的基础：Runner 可以脱离 Loop 单独测试。

---

## 1. AgentLoop

`agent/loop.py:196`，2388 行的核心引擎。类自身的文档字符串就是它的职责清单（`agent/loop.py:198-206`）：

```python
"""The agent loop is the core processing engine.
It:
1. Receives messages from the bus
2. Builds context with history, memory, skills
3. Calls the LLM
4. Executes tool calls
5. Sends responses back
"""
```

### 1.1 装配：依赖全部由外部注入

```python
# cli/agent.py:171
agent_loop = agent_loop_class.from_config(
    runtime_config, bus, provider=provider, cron_service=cron,
    tool_registry=tools, ...,
)
```

`AgentLoop.__init__`（`agent/loop.py:263`）接收的是**已构造好的依赖**（bus / provider / cron / ToolRegistry /
SessionManager），`from_config`（`agent/loop.py:458`）只是把 config 翻译成这些依赖。设计上值得记两点：

1. **ToolRegistry 由调用方持有**，这样 MCP 和 AgentLoop 可以共用同一张注册表，MCP 生命周期不归 Loop 管。
2. Loop 内部再构造三类协作对象：

| 协作对象 | 位置 | 作用 |
| --- | --- | --- |
| `AgentRunner` | `agent/loop.py:385` | 真正的模型-工具循环 |
| `Consolidator` | `agent/loop.py:433` | 记忆归档 / 摘要检查点 |
| `AutoCompact` | `agent/loop.py:443` | 空闲会话的主动压缩 |
| `ToolRegistry` 默认工具 | `agent/loop.py:615` `_register_default_tools` | 文件 / shell / web / 会话 / 子 agent 等内置工具 |

### 1.2 消息接收：`run()` 是一个「取消息 + 派发任务」的循环

```python
# agent/loop.py:1261
async def run(self) -> None:
    """Run the agent loop, dispatching messages as tasks to stay responsive to /stop."""
    self._running = True
    while self._running:
        try:
            msg = await asyncio.wait_for(self.bus.consume_inbound(), timeout=1.0)
        except asyncio.TimeoutError:
            self._check_expired_sessions_if_due()   # 空闲检查（AutoCompact）
            continue
```

关键点：

- **1 秒超时不等于结束**：没有消息时利用这次超时做空闲会话检查（`agent/loop.py:1249` → `auto_compact.check_expired`），
  因此「压缩」不需要独立线程/定时器。
- **命令直通**：`/stop`、`/compact` 等命令不排队，直接 `_dispatch_command_inline`（`agent/loop.py:780`），
  否则用户就没法中断正在跑的任务。
- **同一会话的新消息进入 pending queue**（`agent/loop.py:1361`）：如果该 session 已有活跃任务，
  新消息不会另起任务，而是投递到该轮的注入队列（上限 20，`agent/loop.py:1417`），由 Runner 在安全点插入。
- 其它情况才 `asyncio.create_task(self._dispatch(msg))`（`agent/loop.py:1378`），即「一个 turn 一个任务」。

### 1.3 并发模型：会话内串行、跨会话并发

```python
# agent/loop.py:1391
async def _dispatch(self, msg: InboundMessage) -> None:
    """Process a message: per-session serial, cross-session concurrent."""
    lock = self._get_session_lock(session_key)      # 每个 session 一把锁
    gate = self._concurrency_gate or nullcontext()  # 全局并发闸门（可选）
    async with lock, gate:
        ...
```

| 机制 | 位置 | 说明 |
| --- | --- | --- |
| session 锁 | `agent/loop.py:2382` `_get_session_lock` | 弱引用字典缓存 `asyncio.Lock`，同一会话严格串行 |
| 全局闸门 | `agent/loop.py:430` | `NANOBOT_MAX_CONCURRENT_REQUESTS`（≤0 表示不限） |
| pending queue | `agent/loop.py:1417` | 一轮进行中的追加消息，Runner 在「工具执行后 / 最终回答前」插入 |
| 残留重投 | `agent/loop.py:1490` 起 | turn 结束后队列里剩余的消息重新 `publish_inbound`，不会丢 |

### 1.4 一轮 turn 的 7 个阶段

`_process_message`（`agent/loop.py:1594`）把一轮对话拆成流水线，每个阶段都可以单独计时与失败定位
（`_run_turn_stage`，`agent/loop.py:1689`，会打印 `[turn <id>] Stage <name> completed in ...ms`）：

```python
# agent/loop.py:1689
await self._run_turn_stage(ctx, "restore",  self._restore_turn)
await self._run_turn_stage(ctx, "compact",  self._compact_session)
if await self._run_turn_stage(ctx, "command", self._dispatch_command):
    return ctx.outbound                      # 命令已处理完，不再进模型
await self._run_turn_stage(ctx, "build",   self._build_turn)
await self._run_turn_stage(ctx, "run",     self._run_turn)
await self._run_turn_stage(ctx, "save",    self._persist_turn)
await self._run_turn_stage(ctx, "respond", self._prepare_outbound)
```

| 阶段 | 位置 | 做什么 |
| --- | --- | --- |
| `restore` | `agent/loop.py:1748` | 恢复上次未完成 turn 的 checkpoint、处理附件引用 |
| `compact` | `agent/loop.py:1805` | `AutoCompact.prepare_session`：等待/触发空闲压缩，拿到 `pending_summary` |
| `command` | `agent/loop.py:1813` | 命中斜杠命令就走 `CommandRouter`，返回 `True` 表示这轮结束 |
| `build` | `agent/loop.py:1865` | 解析 runtime、准备 provider 状态、**组装 `TranscriptInput`** |
| `run` | `agent/loop.py:1974` | 调 `_run_agent_loop` → `AgentRunner.run`，拿回结果 |
| `save` | `agent/loop.py:2014` | 把新消息、摘要检查点、usage、latency 写回 session |
| `respond` | `agent/loop.py:2063` | 生成 `OutboundMessage`，交给 `TurnDelivery` 发送/流式收尾 |

这些字段都由一个可变的 `TurnContext`（`agent/loop.py:132`）在阶段之间传递：`session`、`history`、
`transcript_input`、`provider_state`、`final_content`、`stop_reason`、`usage`……
`require_session()` / `require_runtime()`（`agent/loop.py:183`、`189`）用显式报错代替隐式空值。

### 1.5 Session 如何管理

- session key 是 `channel:chat_id`（`session/manager.py:276` 的 `Session.key` 注释），由
  `_effective_session_key`（`agent/loop.py:901`）计算，`unified_session` 打开时所有频道共用一个 key。
- 取会话：`self.sessions.get_or_create(key)`（`SessionManager`，`session/manager.py:1644`），
  内存里是 LRU 强缓存 + 弱引用溢出缓存，磁盘上是 JSONL（`JsonlSessionStore`，`session/manager.py:548`）。
- 会话里存什么：`messages`（可重放的转录）、`metadata`（含 `_last_summary`、`_last_usage`）、
  `last_archived`（已归档边界）、**`provider_state`（provider 原生的继续状态，如 Anthropic 的 compaction）**。
- 保存：`_save_turn`（`agent/loop.py:2140`）只追加「本轮新增的消息」，并在正确的下标位置
  `commit_summary_checkpoint`（`session/manager.py:323`）插入隐藏的摘要边界。

> 关键设计：**持久化的是「完整转录 + 隐藏边界」**。压缩不删除消息，只是把 `last_archived`
> 之后的内容作为重放起点，因此历史永远可回溯（详见 `docs/memory.md`）。

### 1.6 Context 如何生成

`build` 阶段只准备「原料」，真正的 prompt 由 Runner 在需要发请求时组装：

```python
# agent/loop.py:1113
transcript_builder = partial(
    self.context.build_transcript,
    channel=request_ctx.channel,
    workspace=effective_scope.project_path,
    include_memory=session.policy.persist if session is not None else True,
)
```

- `TranscriptInput`（`agent/context.py:73`）＝ `history` + `current_message` + `media` + `session_summary`。
- `history` 来自 `session.get_history()`（`session/manager.py:344`），已按 `last_archived` 与摘要边界裁剪。
- 为什么不在 Loop 里直接拼 prompt？因为上下文预算与压缩发生在**每次模型请求**之前，
  属于 Runner 的职责（见 `docs/context.md`）。

### 1.7 如何调用 Runner

`_run_agent_loop`（`agent/loop.py:939`）把 Loop 侧的一切翻译成一个 `AgentRunSpec`（`agent/runner.py:89`）：

| `AgentRunSpec` 字段 | Loop 传什么 | 对应能力 |
| --- | --- | --- |
| `runtime` | `LLMRuntime`（provider + model + context window + max tokens） | 模型选择 |
| `max_iterations` / `max_tool_result_chars` | config 的 `maxToolIterations`(默认 200) 等 | 循环与结果长度上限 |
| `transcript_input` + `transcript_builder` | 上面的原料 + 构造函数 | 上下文组装 |
| `hook` | `build_agent_turn_hook(...)` | 流式、进度、日志等旁路 |
| `consolidate_history` | `Consolidator.summarize_transcript` | 请求超预算时的 in-turn 压缩 |
| `injection_callback` / `terminal_injection_callback` | `_drain_pending` / `_wait_for_pending` | 中途注入消息 |
| `checkpoint_callback` | `_checkpoint` → `session.provider_state` | 崩溃恢复 |
| `finalize_on_max_iterations` | `turn_continuation.should_finalize_on_max_iterations(...)` | 到上限后是否再要一个总结 |

调用点：`agent/loop.py:1170` `result = await self.runner.run(AgentRunSpec(...))`。
Runner 返回 `AgentRunResult`（`agent/runner.py:118`，含 `final_content` / `stop_reason` / `usage` /
`provider_state` / `summary_checkpoint`），Loop 把它写进 `TurnContext`（`agent/loop.py:1997` 起）。

### 1.8 Response 如何返回

两条出口，都收敛到 `OutboundMessage`：

- 普通对话：`_assemble_outbound`（`agent/loop.py:1716`）组装 `OutboundMessage`，
  若本轮内容已经边流边发（`streamed_content=True`），则打上 `StreamedResponseEvent` 标记，
  避免频道重复发送整段文本。
- 内部/后台 turn（`TurnKind.SYSTEM`）：`delivery.background_response(...)`（`agent/loop.py:2073`）。

发送与「流式卡片收尾」由 `TurnDelivery`（`agent/turn_delivery.py`）负责，
`_dispatch` 在 finally 里兜底：`delivery.complete(...)` / `delivery.fail(...)` / `delivery.idle()`。

---

## 2. AgentRunner

`agent/runner.py:141`，1374 行。类文档字符串一句话：
「Run a tool-capable LLM loop **without product-layer concerns**.」

### 2.1 输入输出契约

```python
# agent/runner.py:89（节选）
class AgentRunSpec:
    initial_messages: list[dict[str, Any]] | None   # 与 transcript_input 二选一
    tools: ToolRegistry
    runtime: LLMRuntime
    max_iterations: int
    max_tool_result_chars: int
    ...

# agent/runner.py:118（节选）
class AgentRunResult:
    final_content: str | None
    messages: list[dict[str, Any]]      # 本轮产生的完整消息序列
    tools_used: list[str]
    stop_reason: str = "completed"      # completed / max_iterations / error / empty_final_response ...
    usage: LLMUsage | None
    round_usages: list[LLMUsage]
    provider_state: ProviderConversationState | None
```

`run()`（`agent/runner.py:308`）只做三件事：构造初始 transcript、在 try/except 里调 `_run_core`、
把结果与异常翻译成 `AgentHook` 事件（`before_run` / `after_run` / `on_error` / `on_finally`）。

### 2.2 主循环

```python
# agent/runner.py:435
for iteration in range(spec.max_iterations):
    await hook.before_iteration(context)
    response, raw_usage = await self._request_model(spec, request_messages, hook, context, ...)
    if response.should_execute_tools:
        ...  # 1) 追加 assistant(tool_calls) 2) 执行工具 3) 追加 tool 结果 4) continue
    ...      # 否则进入「最终回答」分支：空回复重试 / 长度续写 / 注入检查 / break
else:
    stop_reason = "max_iterations"    # for/else：跑满上限才会到这里
```

一次迭代的两条分支：

| 分支 | 条件 | 行为 |
| --- | --- | --- |
| 工具分支 | `response.should_execute_tools` | 追加 assistant 消息（带 `tool_calls`）→ `execute_tool_calls` → 为每个调用追加 `role="tool"` 消息 → 发 `awaiting_tools` / `tools_completed` checkpoint → 继续下一轮 |
| 结束分支 | 没有工具调用 | 处理空回复、`finish_reason == "length"` 的续写、检查中途注入，最终 `break` |

### 2.3 LLM 调用

`_request_model`（`agent/runner.py:865`）在真正发请求前先过一遍**上下文治理**：

```python
# agent/runner.py:877
tool_definitions = spec.tools.get_definitions()
messages, provider_context = await self.context_governor.prepare_request(
    request_state, messages, tool_definitions=tool_definitions, transcript=transcript,
)
```

即：模型看到的 `messages` 是原始 transcript 的**拟合副本**，原始 transcript 不动（细节见 `docs/context.md`）。
随后按 provider 能力选择流式或非流式、处理 reasoning 块、重试与错误映射；
工具调用参数解析失败会走 `_drop_malformed_tool_calls`（`agent/runner.py:1077`）与重试消息（`agent/runner.py:1115`）。

### 2.4 Tool calling 与错误回灌

工具执行本身在 `agent/tools/execution.py`（见 `docs/tool-system.md`），Runner 侧只需要知道
「把结果变成一条 tool 消息」：

```python
# agent/runner.py:533（节选）
tool_message = {
    "role": "tool",
    "tool_call_id": tool_call.id,
    "name": tool_call.name,
    "content": self.context_governor.normalize_tool_result(
        governance_config, tool_call.id, tool_call.name, result,
    ),
}
messages.append(tool_message)
```

四个关键点：

1. **工具失败不抛异常给用户**：失败被转成带 `[Analyze the error above and try a different approach.]`
   提示的文本，模型下一轮自行纠偏（`execution.py:_with_retry_hint`）。
2. **结果先归一化再入上下文**：超长结果按 `max_tool_result_chars` 截断/占位，避免一条 `cat` 撑爆上下文。
3. **重复调用会被拦**：`repeated_external_lookup_error` / `repeated_workspace_violation_error`
   对同一目标反复尝试做节流（`agent/tools/execution.py:123`、`agent/tools/execution.py:262`）。
4. **工具按并发安全性分批**：`_partition_tool_batches`（`agent/tools/execution.py:292`）只把
   `concurrency_safe`（只读且非独占）的工具并到一批 `asyncio.gather`，其余串行——保证结果顺序与副作用可控。

### 2.5 终止条件与「无限循环」的防线

| 常量 / 机制 | 位置 | 作用 |
| --- | --- | --- |
| `spec.max_iterations` | `agent/runner.py:435` | 硬上限（默认来自 config `maxToolIterations = 200`，`config/schema.py:129`） |
| `for ... else` | `agent/runner.py:790` | 跑满上限后设 `stop_reason="max_iterations"`（`agent/runner.py:791`），再决定是否补一次「无工具总结」（`_try_finalize_after_max_iterations`，`agent/runner.py:1163`） |
| `_MAX_EMPTY_RETRIES = 2` | `agent/runner.py:71` | 空回复重试次数，超限改走 finalization 请求 |
| `_MAX_LENGTH_RECOVERIES = 3` | `agent/runner.py:72` | `finish_reason=length` 的续写次数（拼接为同一条回答） |
| `_MAX_INJECTIONS_PER_TURN = 3` | `agent/runner.py:73` | 单次注入取多少条消息 |
| `_MAX_INJECTION_CYCLES = 5` | `agent/runner.py:74` | 一轮内允许的注入循环次数，防止用户消息无限延长一轮 |
| `stop_reason` | `agent/runner.py:118` | 交给上层决定如何对用户表达（如预算耗尽提示） |

### 2.6 中途注入（用户插话 / 子 agent 回传）

两个回调构成了「一轮内接收新输入」的通道（Loop 传入，Runner 在安全点调用）：

- `injection_callback`：**不等待**，只取已经到达的消息，插入在「工具执行之后」或「最终回答之前」。
- `terminal_injection_callback`：只有在本轮即将结束时才**等一会儿**，用于等待子 agent 的完成事件
  （`_SUBAGENT_TERMINAL_WAIT_SECONDS = 300`，`agent/loop.py:124`）。

插入点由 `_try_drain_injections`（`agent/runner.py:155`）统一处理，并且会保持流式卡片存活
（`on_stream_end(resuming=True)`），避免频道提前把卡片收尾。

### 2.7 Checkpoint 与崩溃恢复

在三个时刻通过 `checkpoint_callback` 落盘（`agent/runner.py:1346` `_emit_checkpoint`）：

| phase | 时机 | 用途 |
| --- | --- | --- |
| `awaiting_tools` | 模型要求调工具、工具还没跑 | 崩溃后知道「工具没执行」，不会重复产生副作用 |
| `tools_completed` | 工具已执行、结果已入消息 | 崩溃后可直接续跑，工具结果不丢 |
| `final_response` | 最终回答已产生 | 正常完成的落点 |

Loop 侧把它写进 `session.provider_state` 与 `_meta`（`agent/loop.py:968` `_checkpoint`），
恢复逻辑在 `session/recovery.py`。

---

## 3. 从上游到 myagent：迁移取舍

| 上游机制 | 迁移计划 | 理由 |
| --- | --- | --- |
| Loop / Runner 分离 | **保留** | 职责边界清晰，且能单独测试 Runner |
| `TurnContext` + 阶段流水线 | 保留思想，阶段先简化 | 阶段化让计时与失败定位非常直接，但 7 个阶段里 `restore`（崩溃恢复）在 V1 可以先不做 |
| session 锁 + 全局闸门 + pending queue | 保留锁与注入队列 | 「会话内串行、跨会话并发」是正确性前提；全局闸门可后置 |
| `TurnDelivery` / 多频道投递 | 简化 | 我们只有 CLI 与垂直 Agent，不需要 5 种频道的投递适配 |
| checkpoint 三阶段恢复 | 先简化，Phase 9 再补 | 需要持久化 provider 状态，属于工程化范畴 |
| 子 agent、cron、本地触发器、自动化 turn | 不做 | 已在 `PLAN.md` 2.2 节列为非重点 |
| 工具并发分批 | 保留（只读并发） | 实现成本低，收益明确 |

## 4. 速查索引

| 主题 | 位置 |
| --- | --- |
| Loop 类与职责注释 | `agent/loop.py:196` |
| 消息接收循环 | `agent/loop.py:1261` |
| 会话内串行 / 跨会话并发 | `agent/loop.py:1391` |
| 7 阶段流水线 | `agent/loop.py:1594`、`1689` |
| 上下文原料组装 | `agent/loop.py:1865`、`1113` |
| Loop → Runner 调用 | `agent/loop.py:939`、`1173` |
| 落盘 | `agent/loop.py:2014`、`2140` |
| 出站组装 | `agent/loop.py:1716`、`2063` |
| Runner 契约 | `agent/runner.py:89`、`118` |
| Runner 主循环 | `agent/runner.py:386`、`435` |
| 模型调用 | `agent/runner.py:865` |
| 工具结果回灌 | `agent/runner.py:533` |
| 上限与终止 | `agent/runner.py:71-74`、`790` |
| 注入 | `agent/runner.py:155`、`238` |
