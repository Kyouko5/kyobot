# Phase 2 设计：MyAgent Framework V1

> **锚点约定**：`src/myagent/agent/loop.py:128` 指本仓库源码第 128 行；
> 不带 `src/` 前缀的 `agent/loop.py:1594` 指上游只读参照 `nanobot/nanobot/agent/loop.py`。
> 校验命令：`.venv/bin/python scripts/check_doc_anchors.py`。
>
> **定位**：Framework V1——**能独立跑通，但不追求模块解耦**。解耦是 Phase 3 的任务，
> 所以本文的很多「这样做」后面都会补一句「Phase 3 会怎么改」。
>
> 阅读顺序：先看 §1（模块地图）、§2（一次请求的路径），再看 §3（模块契约）与 §4（与上游的差异表）。

## 0. 一句话

Phase 2 把 nanobot 的 Agent Runtime 拆成 5 层（types / models / tools / agent / session），
用一条 4 阶段流水线（build → run → save → respond）串起来，用 1 个协议 + 1 个实现接模型，
并提供 `myagent chat` 命令行。**把 `nanobot/` 删掉，框架照常工作。**

验收证据（真实运行记录）：`docs/records/phase-2-migration.md`。

## 1. 模块地图

```text
                      ┌─────────────────────────────┐
   用户/CLI ──────────▶│ cli.py                      │  argparse：chat / tools
                      │ └ build_agent_loop() 装配    │  src/myagent/cli.py:36
                      └──────────────┬──────────────┘
                                     │ 注入已构造好的依赖
                                     ▼
   MessageBus ──────▶┌─────────────────────────────┐
   (最小实现)         │ agent/loop.py  AgentLoop     │  4 阶段 + 会话锁
   src/myagent/agent/loop.py:42        │  build → run → save → respond│  src/myagent/agent/loop.py:106
                      └───┬─────────┬─────────┬─────┘
                          │         │         │
        ┌─────────────────┘         │         └──────────────────┐
        ▼                           ▼                            ▼
┌──────────────────┐      ┌──────────────────┐        ┌────────────────────┐
│ agent/context.py │      │ agent/runner.py  │        │ session/manager.py │
│ ContextBuilder   │      │ AgentRunner      │        │ SessionManager     │
│ 系统提示 + 历史   │      │ 模型↔工具循环     │        │ JSONL 追加式存储    │
│ + 当前消息        │      │ 迭代/超时/截断    │        │ + last_archived     │
└──────────────────┘      └───┬──────────┬───┘        └────────────────────┘
                              │          │
                     ┌────────▼───┐  ┌───▼──────────────┐
                     │ models/    │  │ tools/           │
                     │ BaseModel  │  │ ToolRegistry     │
                     │ (Protocol) │  │ + builtin 4 个工具│
                     └────────────┘  └──────────────────┘
```

依赖方向是单向的（`→` 表示「import」）：

```text
cli.py            → agent.loop
agent.loop        → agent.context, agent.runner, agent.types, session.manager,
                    models.base(协议), tools.registry, config
agent.runner      → agent.types, models.base(协议), tools.base, tools.registry
agent.context     → agent.types
tools.registry    → tools.base
session.manager   → agent.types, config
models.openai_compat → models.base, agent.types, config, openai(SDK)
memory.*          → 无（Phase 4 才接线）
```

两条硬约束：

1. **`agent` 不 import `models`**，只有 `models` import `agent.types`。
   否则 `LLMResponse` 需要 `Message`/`Usage`、而 `Message` 需要 `ToolCallRequest`，会形成循环导入；
   解法是把 `Message` / `Usage` / `ToolCallRequest` 放在 `agent/types.py`
   （`src/myagent/agent/types.py:100`、`:61`、`:30`），由 `models/base.py` 转出
   （`src/myagent/models/base.py:23`）。
2. **runner 不 import `openai`**，只认 `LLMError` / `ContextWindowExceeded`。
   换提供方 = 换一个 `BaseModel` 实现，runner 一行不改。

### 1.1 文件职责

| 文件 | 职责 | 关键符号 | 上游对应 |
| --- | --- | --- | --- |
| `agent/types.py` | 全runtime 唯一的消息/用量/事件表示 | `Message` `Usage` `StopReason` `ToolCallRequest` `InboundMessage` `OutboundMessage` | `providers/base.py` 的 `ToolCallRequest` / `LLMResponse` + `bus/events.py` |
| `models/base.py` | 模型接口与错误语义 | `BaseModel` `LLMResponse` `LLMError` `ContextWindowExceeded` | `providers/base.py:LLMProvider`（大幅收窄） |
| `models/openai_compat.py` | OpenAI 兼容实现 + 错误翻译 | `OpenAICompatModel` `to_openai_messages` | `providers/openai_compat_provider.py` |
| `tools/base.py` | 工具契约、JSON Schema 校验/转换 | `Tool` `ToolResult` `validate_schema_value` | `agent/tools/base.py` |
| `tools/registry.py` | 注册/暴露/准备/执行 | `ToolRegistry.prepare_call` `.execute` `.get_definitions` | `agent/tools/registry.py` |
| `tools/builtin/` | 4 个内置工具（显式注册） | `build_default_registry` | `agent/loop.py:_register_default_tools` |
| `agent/runner.py` | 模型↔工具循环 | `AgentRunSpec` `AgentRunResult` `AgentRunner` | `agent/runner.py` |
| `agent/context.py` | 组装一次请求的 messages | `ContextBuilder` `ContextBundle` | `agent/context.py`（Phase 6 重写为 ContextManager） |
| `session/manager.py` | JSONL 会话存储 | `Session` `SessionManager` | `session/manager.py:JsonlSessionStore` |
| `agent/loop.py` | 一轮对话的 4 阶段 + 会话锁 + 最小 Bus | `AgentLoop` `TurnContext` `MessageBus` | `agent/loop.py:AgentLoop`（7 阶段） |
| `memory/base.py` `memory/store.py` | V1 记忆接口 + 文件实现（**未接进 Loop**） | `MemoryRecord` `MemoryStore` `FileMemoryStore` | `agent/memory.py` |
| `cli.py` | 命令行入口与装配 | `main` `build_agent_loop` | `cli/agent.py` |

## 2. 一次请求经过哪些代码

```text
myagent chat -m "算一下 (12+8)*3，再读一下 workspace/project-notes.md"
  │
  │ cli._chat()                                   src/myagent/cli.py:90
  │  ├ 先校验 LLM_MODEL / LLM_API_KEY，缺了直接退出码 2
  │  └ asyncio.run(loop.run_once(text, session_key))
  ▼
AgentLoop._process()                             src/myagent/agent/loop.py:141
  │  async with self._session_lock(session_key):       ← 会话内串行
  │
  ├─[build]   _build_turn()                      src/myagent/agent/loop.py:151
  │     SessionManager.get_or_create() → Session（JSONL 懒加载）
  │     ContextBuilder.build() → [system, *history, user]  src/myagent/agent/context.py:66
  │
  ├─[run]     _run_turn()                        src/myagent/agent/loop.py:156
  │     AgentRunSpec(messages, tools, model, 3 个上限)
  │     └ AgentRunner.run()                      src/myagent/agent/runner.py:90
  │          for iteration in range(max_iterations):
  │            ① 取注入消息（injection_callback，V1 未启用）
  │            ② model.generate(messages, tools=definitions)
  │            ③ 没有 tool_calls → 完成（空回复最多重试 2 次）
  │            ④ 有 tool_calls → 按「只读可并发」分批执行
  │                 ToolRegistry.execute() → ToolResult
  │                 超长截断 / 错误回灌 → Message.tool(...)
  │            ⑤ 跑满上限 → 补一次「不带工具」的收尾请求
  │
  ├─[save]    _save_turn()                       src/myagent/agent/loop.py:170
  │     只追加本轮新增消息（系统提示永不落盘）→ data/sessions/cli%3Adefault.jsonl
  │
  └─[respond] _prepare_outbound()                src/myagent/agent/loop.py:184
        AgentRunResult → OutboundMessage(content, stop_reason, tools_used)
        → run_once() 返回文本 / run() 投递到 bus
```

关键点：

- **四个阶段各自计时并打日志**（`src/myagent/agent/loop.py:196`，对应上游 `agent/loop.py:1689`）。
  一条 `MYAGENT_LOG_LEVEL=DEBUG` 的运行能看到 `build 0.2ms / run 4252.7ms / save 1.3ms / respond 0.0ms`。
- **只有「本轮新增」的消息写盘**：`ContextBundle.transcript_start`
  （`src/myagent/agent/context.py:34`）指向本轮的 user 消息，它之前的系统提示（每轮重建）与已存历史
  都不再重复落盘——`_save_turn`（`src/myagent/agent/loop.py:170`）只追加
  `messages[transcript_start:]` 加 runner 新产出的消息。
- **会话内串行、跨会话并发**：`_session_lock()`（`src/myagent/agent/loop.py:205`）按 session_key 缓存
  `asyncio.Lock`，与上游同一思路（`agent/loop.py:1391`、`agent/loop.py:2382`）。

## 3. 模块契约

### 3.1 agent/types.py：一个消息表示，三个消费方

`Message`（`src/myagent/agent/types.py:100`）同时是「给模型的请求」「模型的回答」「落盘的转录」。
上游有三套并行表示（dataclass、provider 请求 dict、JSONL 记录），字段容易在层之间丢失；
V1 只保留一套 dataclass + 两个显式转换器：

```text
Message ──to_openai_messages()──▶ OpenAI 请求体        （src/myagent/models/openai_compat.py:47）
Message ──to_dict()/from_dict()─▶ JSONL 记录与回放     （src/myagent/agent/types.py:155、src/myagent/agent/types.py:136）
```

角色只有四种：`system` / `user` / `assistant`（可带 `tool_calls`）/ `tool`（带 `tool_call_id`）。
构造用 `Message.system/user/assistant/tool`（`src/myagent/agent/types.py:114`），避免手写 role 字符串。

`StopReason`（`src/myagent/agent/types.py:90`）用 `StrEnum`，取值就是 PLAN 2.4 要求的四个：
`completed` / `max_iterations` / `error` / `empty_final_response`；用枚举而不是裸字符串，
是为了让「终止原因」在类型层面可检查，同时因为它继承 `str`，写日志/落盘仍然直白。

### 3.2 models/：一个协议 + 一个实现

```python
class BaseModel(Protocol):                        # src/myagent/models/base.py:71
    async def generate(...) -> LLMResponse      # src/myagent/models/base.py:74
    def stream(...) -> AsyncIterator[str]       # :88   V1 只留接口
    def count_tokens(...) -> int | None         # :97   Phase 6 预算用
```

- `LLMResponse`（`src/myagent/models/base.py:44`）带 `should_execute_tools`
  （`src/myagent/models/base.py:58`）：只有 `finish_reason ∈ {tool_calls, function_call, stop}` 才执行工具，
  与上游 `providers/base.py:604` 的判定一致——网关在 `refusal` 下塞的 tool_calls 不能执行。
- 错误只有两种语义：`LLMError`（其它一切）与 `ContextWindowExceeded`
  （`src/myagent/models/base.py:31`、`:35`）。**跑满上下文的处理属于 Phase 6**，
  V1 把它转成 `stop_reason="error"` 加一句明确的话，不做长度续写。
- 工具调用参数解析失败不抛异常：`ToolCallRequest.parse_error`
  （`src/myagent/agent/types.py:41`）把问题带到 runner，变成一次可读的工具错误。
  上游用 `parse_tool_arguments()`（`providers/base.py:110`）保留原始值、由 registry 拒绝，
  思路相同，只是 V1 把「为什么不可用」写成了字段。

`OpenAICompatModel`（`src/myagent/models/openai_compat.py:62`）的三个细节：

| 细节 | 位置 | 原因 |
| --- | --- | --- |
| 凭据延迟到首次请求才解析 | `src/myagent/models/openai_compat.py:75` | `myagent tools` 等离线命令无需 key；测试可注入假客户端 |
| 异常→框架错误集中翻译 | `src/myagent/models/openai_compat.py:123` | 超时/连接/鉴权/限流/上下文超限/其它 HTTP 错误各有独立文案 |
| 认证类失败也识别为「上下文超限」 | `src/myagent/models/openai_compat.py:142` | 提供方既可能给 `error.code`，也可能只在文案里写 "maximum context length" |

### 3.3 tools/：契约 → 校验 → 执行

```text
ToolResult(str)            src/myagent/tools/base.py:36    str 子类 + is_error，工具失败不抛异常
Tool(ABC)                  src/myagent/tools/base.py:146   name/description/parameters/read_only/exclusive
 ├ to_schema()             :175                 → OpenAI function 定义
 ├ cast_params()           :186                 "3" → 3（按 schema 纠正类型）
 ├ validate_params()       :193                 → 人类可读的错误列表
 └ concurrency_safe        :166                 read_only and not exclusive
validate_schema_value()    :117                 JSON Schema 子集的校验实现
```

`ToolRegistry`（`src/myagent/tools/registry.py:22`）必须做到四件事：

| 能力 | 位置 | 行为 |
| --- | --- | --- |
| `register` / `get` / `tool_names` | `:29`、`:39`、`:48` | 显式注册，不自动发现 |
| `get_definitions` | `:52` | **按名字排序**，让工具块在 prompt 缓存下保持稳定（上游 `agent/tools/registry.py:86`） |
| `prepare_call` | `:60` | 名字纠错（`DEMO` → `demo`）+ 类型纠正 + schema 校验，**任何情况都不抛异常**，错误以文本返回 |
| `execute` | `:102` | 兜底入口：内部异常 → `ToolResult.error`，并追加 `[Analyze the error above and try a different approach.]`（`src/myagent/tools/registry.py:149`，与上游 `agent/tools/execution.py:_RETRY_HINT` 同句） |

四个内置工具（`src/myagent/tools/builtin/__init__.py:30` 显式注册）：

| 工具 | 只读 | 参数 | 说明 |
| --- | --- | --- | --- |
| `calculator` | ✅ | `expression` | AST 白名单求值，**不用 `eval`**；限制指数 ≤ 64，除零/溢出转成工具错误（`src/myagent/tools/builtin/calculator.py:52`） |
| `current_time` | ✅ | `timezone`(可选) | `zoneinfo` 时区名，默认本地（`src/myagent/tools/builtin/current_time.py:37`） |
| `read_file` | ✅ | `path`, `max_lines` | 工作区限定 + 行号 + 截断提示（`src/myagent/tools/builtin/read_file.py:48`） |
| `search_local` | ✅ | `query`, `limit` | 受控本地检索桩：字面量、大小写不敏感、跳过隐藏目录/超大文件；**Phase 7 换成真实检索**（`src/myagent/tools/builtin/search_local.py:60`） |

工作区边界由 `resolve_in_workspace()` 统一实现（`src/myagent/tools/builtin/paths.py:18`）：
拒绝绝对路径，解析符号链接后必须仍在工作区内——这是上游 `security/workspace_access.py` 的 V1 版本。

### 3.4 agent/runner.py：模型 ↔ 工具循环

```python
@dataclass(slots=True)
class AgentRunSpec:            # src/myagent/agent/runner.py:58
    messages: list[Message]
    tools: ToolRegistry
    model: BaseModel
    max_iterations: int
    max_tool_result_chars: int
    tool_timeout_s: float = 30.0
    hooks: list[AgentHook] = field(default_factory=list)
    injection_callback: Callable[[], Awaitable[list[Message]]] | None = None

@dataclass(slots=True)
class AgentRunResult:          # src/myagent/agent/runner.py:72
    final_content: str | None
    messages: list[Message]
    tools_used: list[str]
    stop_reason: StopReason
    usage: Usage | None = None
    error: str | None = None          # 相对 PLAN 的加法：失败原因需要能传到出口
```

终止与容错（`src/myagent/agent/runner.py:90` 的主循环）：

| 机制 | 位置 | 行为 |
| --- | --- | --- |
| 迭代上限 | `src/myagent/agent/runner.py:98` | `for iteration in range(spec.max_iterations)`，与上游同构（`agent/runner.py:435`） |
| 收尾请求 | `src/myagent/agent/runner.py:130` | 跑满上限后追加一条「不要再调工具」的提示，`tools=None` 请求一次总结；该提示**只存在于这次请求**，不写进转录 |
| 工具超时 | `src/myagent/agent/runner.py:181` | `asyncio.wait_for` per call，超时转成 `Error: tool 'x' timed out after Ns`（文案在 `src/myagent/agent/runner.py:185`） |
| 工具错误回灌 | `src/myagent/agent/runner.py:165` | 名称缺失 / 参数畸形 / 工具抛错 / `ToolResult.error` 四种都变成 `tool` 消息，**永不中断本轮** |
| 结果截断 | `src/myagent/agent/runner.py:213` | 超过 `max_tool_result_chars` 截断并加 `\n... (truncated)`（与上游 `utils/helpers.py:371` 同后缀） |
| 空回复重试 | `src/myagent/agent/runner.py:31` | 上限 2 次，超限 → `empty_final_response` |
| 只读并发分批 | `src/myagent/agent/runner.py:220` | 连续 `concurrency_safe` 的调用合成一批 `asyncio.gather`，其余串行；镜像上游 `agent/tools/execution.py:292` |
| 用量累计 | `src/myagent/agent/runner.py:207` | 多轮请求的 `Usage` 相加（上游 `LLMUsage` 也支持多调用聚合） |
| 中途注入 | `src/myagent/agent/runner.py:188` | 每次模型调用前取一次已到达的消息；Loop 侧 V1 不启用（PLAN 2.4） |
| Hook | `src/myagent/agent/runner.py:40` | `on_iteration` / `on_tool_results` 两个观察点，为 Phase 3 的流式/进度留口 |

**本阶段明确不做**（PLAN 2.4）：长度续写、畸形 tool_calls 重试、provider 原生状态、三阶段 checkpoint 恢复。

### 3.5 agent/context.py：此阶段故意很薄

```python
@dataclass(frozen=True, slots=True)
class ContextBundle:      # src/myagent/agent/context.py:34
    messages: list[Message]
    transcript_start: int     # 本轮新增消息的起点（系统提示与已存历史都在它之前）

class ContextBuilder:     # src/myagent/agent/context.py:47
    def system_prompt(self) -> str: ...                      # src/myagent/agent/context.py:53
    def build(self, *, history, user_input) -> ContextBundle: ...   # :66
```

系统提示由三段拼成：身份 → 工具使用指引 → 运行环境（当前时间、工作区根目录）。
Phase 6 会把它重写成「可裁剪的 section」，所以现在**不做**预算计算、不做摘要压缩
（上游对应机制见 `docs/context.md`）。

### 3.6 session/manager.py：JSONL 追加式存储

```text
data/sessions/cli%3Adefault.jsonl
├─ {"type":"session","key":"cli:default","created_at":"...","last_archived":0}   ← 头记录
├─ {"type":"message","message":{"role":"user","content":"..."}}
├─ {"type":"message","message":{"role":"assistant","tool_calls":[...]}}
└─ {"type":"message","message":{"role":"tool","tool_call_id":"...","content":"..."}}
```

- 一个 session 一个文件，文件名是 `quote(key, safe="")`（`src/myagent/session/manager.py:72`），
  `cli:default` → `cli%3Adefault.jsonl`，因此 `a/b` 与 `a-b` 不会撞车。
- **只追加**，不重写：崩溃最多丢掉尾部几条，不会破坏已有历史（上游 `session/manager.py:548`
  的 `JsonlSessionStore` 同样以追加为主）。
- `last_archived` 是**为 Phase 4 预留**的边界字段：压缩时前移即可，不删消息
  （`Session.transcript()` 从该下标开始返回，`src/myagent/session/manager.py:50`）。
- 坏行不致命：无法解析的行打 warning 后跳过（`src/myagent/session/manager.py:107`）。

### 3.7 agent/loop.py：4 阶段 + 锁 + 最小 Bus

| 阶段 | 位置 | 做什么 |
| --- | --- | --- |
| `build` | `src/myagent/agent/loop.py:151` | 取会话（`get_or_create`）+ 组装 messages |
| `run` | `src/myagent/agent/loop.py:156` | 构造 `AgentRunSpec` → `AgentRunner.run()` |
| `save` | `src/myagent/agent/loop.py:170` | 只追加**本轮新增**消息（依赖 `transcript_start`）|
| `respond` | `src/myagent/agent/loop.py:184` | `AgentRunResult` → `OutboundMessage`（含 stop_reason / tools_used） |

与上游 7 阶段（`agent/loop.py:1594`）的差别：`restore`（崩溃恢复）与 `compact`（空闲压缩）
合并进 `build`，并且 V1 没有 `command` 阶段——`/exit`、`/session`、`/clear` 由 CLI 自己处理
（上游把命令放在模型调用之前的原因是一样的：命令必须在模型不可用时也能生效）。

`MessageBus`（`src/myagent/agent/loop.py:42`）是 Phase 2 的最小实现：两个 `asyncio.Queue`，
`run()`（`src/myagent/agent/loop.py:133`）消费到 `None` 哨兵就返回。上游的 Bus 要负责
5 种频道、cron、子 agent 的扇出，V1 只需要「一条进、一条出」。

**失败的一轮也走完 4 个阶段**（这是有意的）：模型调用失败时 runner 返回
`stop_reason="error"` 而不是抛异常（`src/myagent/agent/runner.py:103`），于是 `save` 仍会写下
这条用户消息，`respond` 给出一句可读的 `The model call failed: ...`
（`src/myagent/agent/loop.py:213`）。代价是历史里会留下一条**没有回答的用户消息**——
下一轮请求因此可能出现连续两条 user 消息（OpenAI 兼容端点接受），换来的是「用户说过什么」
不会因为一次网络抖动而丢失。Phase 9 若要做重试/补偿，应该在这一层加。

### 3.8 memory/：接口先立住，Phase 4 接线

`MemoryStore`（`src/myagent/memory/base.py:51`）定义了 `add` / `search` / `all` / `clear`；
`FileMemoryStore`（`src/myagent/memory/store.py:21`）是「一个 JSONL + 字面量检索」的最小实现。
**它没有接进 Loop**：检索策略、写入时机、三层模型（working / episodic / semantic）都是 Phase 4 的设计，
现在接进去只会制造 Phase 3 需要拆掉的耦合。

### 3.9 config 与 CLI

- `LLMSettings`（`src/myagent/config/settings.py:258`）：`LLM_PROVIDER` / `LLM_MODEL` / `LLM_API_KEY` /
  `LLM_BASE_URL` / `LLM_MAX_TOKENS` / `LLM_CONTEXT_WINDOW` / `LLM_TEMPERATURE`。
  `from_env()` 只校验形状，**不要求** model/key 存在，`require_model()` / `require_api_key()`
  在真正要发请求时才失败——这样离线命令照常可用。
- `AgentSettings`（`src/myagent/config/settings.py:339`）：`AGENT_MAX_ITERATIONS`（12）/
  `AGENT_TOOL_TIMEOUT_S`（30）/ `AGENT_MAX_TOOL_RESULT_CHARS`（8000）/ `AGENT_WORKSPACE`（`workspace`）/
  `AGENT_SESSIONS_DIR`（`data/sessions`）。
- `myagent chat -m "..."` / `myagent chat`（交互：`/exit`、`/session`、`/clear`）/ `myagent tools`
  （`src/myagent/cli.py:61`）。`myagent tools` 不需要任何凭据：它只读 `AgentSettings`
  并打印注册表内容（`src/myagent/cli.py:78`）。
- 装配集中在 `build_agent_loop()`（`src/myagent/cli.py:36`）。**这是 Phase 2 的一个已知妥协**：
  装配与命令行混在一个文件里，Phase 3 会把 `build_agent_loop` 抽成 `runtime.py`。

## 4. 与上游的差异表

### 4.1 保留（同构迁移）

| 机制 | 上游 | myagent | 为什么保留 |
| --- | --- | --- | --- |
| Loop / Runner 分离 | `agent/loop.py:196` / `agent/runner.py:89` | `src/myagent/agent/loop.py:106` / `src/myagent/agent/runner.py:58` | 职责边界清晰，Runner 可单独测试 |
| 阶段化流水线 + 计时 | `agent/loop.py:1689` | `src/myagent/agent/loop.py:196` | 定位慢/坏的一轮不需要调试器 |
| 会话内串行、跨会话并发 | `agent/loop.py:1391` | `src/myagent/agent/loop.py:141`、`:205` | 正确性前提，成本几乎为零 |
| 工具结果「错误即观察」 | `agent/tools/execution.py:_with_retry_hint` | `src/myagent/tools/registry.py:149` | 模型能自我纠偏，一轮不会因工具失败而中断 |
| 只读工具并发分批 | `agent/tools/execution.py:292` | `src/myagent/agent/runner.py:220` | 实现便宜、收益明确（一次 3 个只读调用只花 1 个 RTT 批次） |
| 工具定义按名排序 | `agent/tools/registry.py:86` | `src/myagent/tools/registry.py:52` | prompt 缓存友好 |
| 会话 JSONL 追加式 | `session/manager.py:548` | `src/myagent/session/manager.py:55` | 崩溃不破坏历史 |
| schema 驱动的类型纠正 | `agent/tools/base.py:251` | `src/myagent/tools/base.py:186` | 模型给的 `"3"` 不该导致校验失败 |

### 4.2 简化

| 上游机制 | V1 做法 | 理由 / 后续 |
| --- | --- | --- |
| 7 阶段（含 `restore`/`compact`/`command`） | 4 阶段，命令在 CLI | 崩溃恢复与压缩是 Phase 4/9；CLI 命令只需一个 `if` |
| `TurnContext` 20+ 字段 | 6 个字段（`src/myagent/agent/loop.py:77`） | 只保留本阶段真的会用的；`require_*()` 保证阶段顺序错误立刻暴露 |
| `LLMProvider` 广接口（状态/遥测/重试策略） | `BaseModel` 三个方法 | Phase 6 需要预算时才扩 |
| 工具自动发现 / 插件 / MCP | 显式注册 4 个工具 | PLAN 2.3 明确本阶段不做自动发现 |
| `Schema` 抽象基类 + 装饰器 | 模块级函数 + 每工具显式 `parameters` 属性 | 4 个工具不值得类改写（`agent/tools/base.py:318` 的 `tool_parameters`） |
| 完整 JSON Schema | 子集（type / enum / min·max / minLength·maxLength / required / additionalProperties / 嵌套） | 只实现内置工具用得到的部分，避免把 pydantic 拉进工具层 |
| 5 种频道投递（`TurnDelivery`） | `OutboundMessage` 直出 | 只有 CLI 与垂直 Agent |
| provider 原生状态、checkpoint | 不做 | PLAN 2.4 写明「本阶段不做」 |

### 4.3 相对 PLAN 的加法（都要在评审时能说清）

| 加法 | 位置 | 原因 |
| --- | --- | --- |
| `AgentRunResult.error` | `src/myagent/agent/runner.py:84` | 只有 `stop_reason="error"` 无法把失败原因告诉用户 |
| `ToolCallRequest.parse_error` | `src/myagent/agent/types.py:41` | 「参数解析失败要能被上层看见」（PLAN 2.2）需要一个载体 |
| `stop_reason` 用 `StopReason` 枚举 | `src/myagent/agent/types.py:90` | 是 `str` 子类，不违反 PLAN 的 `str` 约定，但可被类型检查 |
| `AgentHook` 协议 | `src/myagent/agent/runner.py:40` | 给 Phase 3 的流式/进度留口，V1 只有测试与 CLI 用 |
| `MessageBus` 放在 `loop.py` | `src/myagent/agent/loop.py:42` | PLAN 2.1 的文件清单没有 `bus.py`，V1 只有 30 行，Phase 3 再拆 |

## 5. 本阶段不做（Phase 3+ 的输入）

| 未做 | 谁来做 | 现状 |
| --- | --- | --- |
| Loop / Context / Tool / Model / Memory 的可替换接口 | Phase 3 | 目前依赖是构造注入 + 协议，但 Loop 仍直接持有具体对象 |
| `runtime.py` 统一装配 | Phase 3（PLAN 3.5） | 现在在 `cli.build_agent_loop()` |
| 长度续写、上下文预算、`count_tokens` | Phase 6 | `BaseModel.count_tokens()` 已留接口，返回 `None` |
| 上下文压缩、`last_archived` 前移 | Phase 4/6 | 字段已落盘，语义已实现（`Session.transcript()`） |
| 三层记忆与检索 | Phase 4 | `memory/` 只有接口 + 文件实现 |
| 真实论文检索 | Phase 7 | `search_local` 是受控桩 |
| 崩溃恢复 / checkpoint | Phase 9 | 无 |

## 6. 速查索引

| 主题 | 位置 |
| --- | --- |
| 消息与终止原因 | `src/myagent/agent/types.py:100`、`:90` |
| 模型协议与错误 | `src/myagent/models/base.py:71`、`:31`、`:35` |
| OpenAI 兼容实现 / 错误翻译 | `src/myagent/models/openai_compat.py:62`、`:123` |
| 工具契约 / 校验 | `src/myagent/tools/base.py:146`、`:117` |
| 工具注册表（准备/执行/定义） | `src/myagent/tools/registry.py:60`、`:102`、`:52` |
| 内置工具注册 | `src/myagent/tools/builtin/__init__.py:30` |
| Runner 主循环 / 收尾 / 截断 / 分批 | `src/myagent/agent/runner.py:90`、`:130`、`:213`、`:220` |
| Loop 4 阶段 / 锁 / Bus | `src/myagent/agent/loop.py:151`、`:201`、`:42` |
| 上下文组装 | `src/myagent/agent/context.py:34`、`:65` |
| 会话 JSONL / 边界字段 | `src/myagent/session/manager.py:55`、`:107` |
| 记忆接口（未接线） | `src/myagent/memory/base.py:51` |
| CLI 入口 / 装配 | `src/myagent/cli.py:50`、`:36` |
| 设置项 | `src/myagent/config/settings.py:258`、`:339` |
