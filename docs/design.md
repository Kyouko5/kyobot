# MyAgent 设计：Framework V2（模块可替换、依赖可注入）

> **锚点约定**：`src/myagent/agent/loop.py:117` 指本仓库源码第 117 行；
> 不带 `src/` 前缀的 `agent/loop.py:1594` 指上游只读参照 `nanobot/nanobot/agent/loop.py`。
> 校验命令：`.venv/bin/python scripts/check_doc_anchors.py`。
>
> **定位**：Phase 2 交付的是「能独立跑通、但不追求解耦」的 Framework V1；Phase 3 把它改造成
> **Framework V2——模块可替换、依赖可注入**：8 个 `Protocol` 契约 + 1 个装配点
> `build_agent()`。Loop 不再 new 任何具体对象，核心模块也不再 import 任何 SDK。
>
> 阅读顺序：§1 模块地图 → §2 一次请求的代码路径 → §3 模块契约（§3.1 职责表、§3.2 六个扩展点、
> §3.13 装配图）→ §4 与上游的差异表 → §5 尚未实现的能力 → §6 四个设计问题的答辩。
>
> 验收证据（真实运行记录）：`docs/records/phase-3-refactor.md`。

## 0. 一句话

Phase 2 的 Loop 是「构造注入 + 具体类型」：`AgentLoop` 拿到的是 `OpenAICompatModel`、
`ContextBuilder`、`SessionManager`，装配写在 `cli.build_agent_loop()` 里。
Phase 3 做了三件事：

1. **契约化**：跨模块边界改成 `typing.Protocol`，实现方不需要继承任何东西（§3.2、ADR-0007）；
2. **职责收敛**：Loop 只保留 `build → run → save → respond`，拼 prompt / 调模型 / 检索
   全部下沉给对应模块（§3.1、§3.7）；
3. **单一装配点**：`myagent.runtime.build_agent(settings)` 是唯一知道「哪个类实现哪个契约」的
   地方，CLI 只调用它（§3.13）。

判定这三件事真的做到了，靠的不是文档而是 `tests/test_contracts.py`：它用 AST 检查核心模块的
import（`tests/test_contracts.py:435`）、用不继承任何东西的假件驱动整条链路
（`tests/test_contracts.py:305`）。

## 1. 模块地图与依赖方向

```text
                    ┌──────────────────────────────────────────────┐
   用户 / CLI ──────▶│ runtime.build_agent(settings)                │  唯一装配点
                    │ src/myagent/runtime.py:34                    │
                    └───────────────────┬──────────────────────────┘
                                        │ Settings（.env）→ 具体实现
                                        ▼
   MessageBus ──────▶┌──────────────────────────────────────────────┐
   （最小实现）        │ agent/loop.py  AgentLoop                     │
   src/myagent/agent/loop.py:52  │ build → run → save → respond      │
                     │ src/myagent/agent/loop.py:117                │
                     └──┬───────────┬──────────────┬────────────────┘
                        │           │              │
      ContextManager ◀──┘           │              └──▶ SessionStore
      src/myagent/agent/context.py:166    src/myagent/session/base.py:44
                                    ▼
                        AgentRunner（模型 ↔ 工具循环）
                        src/myagent/agent/runner.py:87
                          ├──▶ BaseModel.generate()      src/myagent/models/base.py:73
                          └──▶ ToolRegistry.execute()    src/myagent/tools/registry.py:106
                                  （存的是 BaseTool，src/myagent/tools/base.py:152）
```

依赖方向是单向的（`→` 表示「import」，**粗体**是本阶段新增的边界）：

```text
cli.py                → runtime, agent.loop, config, observability, session.base
runtime.py            → 全部契约 + 全部默认实现（models.openai_compat、
                        tools.builtin、session.manager、agent.context、agent.runtime）
agent.loop            → agent.context(ContextManager), agent.runner, agent.runtime,
                        agent.types, models.base(协议), session.base(协议),
                        tools.registry, observability
agent.runner          → agent.types, models.base(协议), tools.base, tools.registry
agent.context         → agent.types, tokens
agent.runtime         → config.settings
session.manager       → session.base(协议), agent.types, config.settings
models.openai_compat  → models.base(协议), agent.types, config.settings, openai(SDK)
tools.registry        → tools.base(协议)
memory.* / rag.*      → 只定义契约与数据类型（Phase 4/5 才接线）
```

三条硬约束：

1. **`agent` 不 import `models` 的具体实现**，只有 `models` import `agent.types`。
   否则 `LLMResponse` 需要 `Message`/`Usage`、而 `Message` 需要 `ToolCallRequest`，会形成循环导入；
   解法是把 `Message` / `Usage` / `ToolCallRequest` 放在 `agent/types.py`
   （`src/myagent/agent/types.py:100`、`:61`、`:30`），由 `models/base.py` 转出
   （`src/myagent/models/base.py:23`）。
2. **核心不 import 第三方 SDK**：`openai` / `qdrant_client` / `sqlite3` / `httpx` 只允许出现在
   实现文件里。runner 只认 `LLMError` / `ContextWindowExceeded`
   （`src/myagent/models/base.py:31`、`:35`），换提供方 = 换一个 `BaseModel` 实现。
   这条约束由 `tests/test_contracts.py:435` 的 AST 检查强制执行。
3. **具体实现只在装配点碰面**：`myagent.runtime` 是唯一同时 import 契约与实现的模块
   （`tests/test_contracts.py:446` 断言这一点），其余模块只认契约。
   同理，`agent` 也不依赖 `memory` / `rag` 的记录类型：检索结果以
   `ContextItem(text, reference, score)`（`src/myagent/agent/context.py:74`）交给上下文（§3.8）。

### 1.1 文件职责

| 文件 | 职责 | 关键符号 | 上游对应 |
| --- | --- | --- | --- |
| `agent/types.py` | 全 runtime 唯一的消息/用量/事件表示 | `Message` `Usage` `StopReason` `ToolCallRequest` `InboundMessage` `OutboundMessage` | `providers/base.py` 的 `ToolCallRequest` / `LLMResponse` + `bus/events.py` |
| `models/base.py` | 模型契约与错误语义 | `BaseModel` `LLMResponse` `LLMError` `ContextWindowExceeded` | `providers/base.py:LLMProvider`（大幅收窄） |
| `models/openai_compat.py` | OpenAI 兼容实现 + 错误翻译 | `OpenAICompatModel` `to_openai_messages` | `providers/openai_compat_provider.py` |
| `tools/base.py` | 工具契约（`BaseTool` Protocol）与便利基类（`Tool` ABC） | `BaseTool` `Tool` `ToolResult` `validate_schema_value` | `agent/tools/base.py` |
| `tools/registry.py` | 注册/暴露/准备/执行 | `ToolRegistry.prepare_call` `.execute` `.get_definitions` | `agent/tools/registry.py` |
| `tools/builtin/` | 4 个内置工具（显式注册） | `build_default_registry` | `agent/loop.py:_register_default_tools` |
| `agent/runner.py` | 模型↔工具循环 | `AgentRunSpec` `AgentRunResult` `AgentRunner` | `agent/runner.py` |
| `agent/context.py` | 按 section 组装一次请求、估算 token、超预算报错 | `ContextManager` `ContextSection` `SectionedContextManager` | `agent/context.py:89` + `agent/context_governance.py:336` |
| `agent/runtime.py` | 一轮的行为上限 | `AgentRuntimeConfig` | `config/schema.py:129` |
| `session/base.py` | 会话契约：转录 + 压缩边界 | `Session` `SessionStore` `DEFAULT_SESSION_KEY` | `session/manager.py:344` 的 `get_history` |
| `session/manager.py` | JSONL 实现 | `JsonlSessionStore` | `session/manager.py:JsonlSessionStore` |
| `agent/loop.py` | 一轮对话的 4 阶段 + 会话锁 + 最小 Bus | `AgentLoop` `TurnContext` `MessageBus` | `agent/loop.py:AgentLoop`（7 阶段） |
| `rag/` | 检索契约与数据类型（未接线） | `BaseEmbedder` `BaseVectorStore` `BaseRetriever` `Document` `Chunk` | `rag/`（Phase 5 实现） |
| `memory/` | 记忆契约 + 文件实现（未接线） | `MemoryRecord` `BaseMemory` `FileMemoryStore` | `agent/memory.py` |
| `tokens.py` | 全框架共用的 token 估算 | `estimate_tokens` | `utils/token_counter.py` |
| `runtime.py` | **唯一装配点** | `build_agent` | `cli/agent.py` + `agent/loop.py:_register_default_tools` |
| `cli.py` | 命令行入口（不含装配） | `main` `_chat` `_list_tools` | `cli/agent.py` |

## 2. 一次请求经过哪些代码

```text
myagent chat -m "算一下 (12+8)*3，再读一下 workspace/project-notes.md"
  │
  │ cli._chat()                                   src/myagent/cli.py:74
  │  ├ 先校验 LLM_MODEL / LLM_API_KEY，缺了直接退出码 2   src/myagent/cli.py:77
  │  └ loop = build_agent()                         src/myagent/cli.py:83
  │     asyncio.run(loop.run_once(text, session_key))     src/myagent/cli.py:85
  ▼
AgentLoop._process()                             src/myagent/agent/loop.py:152
  │  async with self._session_lock(session_key):       ← 会话内串行（:247）
  │
  ├─[build]   _build_turn()                      src/myagent/agent/loop.py:162
  │     self.sessions.get_or_create() → Session（JSONL 懒加载）
  │     ContextRequest(user_input, history, tools, session_key, budget_tokens)
  │     self.context.build(request) → ContextBundle    src/myagent/agent/context.py:235
  │       ├ 组装 5 个 section（system/conversation/memory/rag/tools）  :204
  │       └ 估算总 token，超预算 → ContextBudgetExceeded        :246
  │     捕获超预算 → ctx.error（本轮不发请求）           src/myagent/agent/loop.py:174
  │
  ├─[run]     _run_turn()                        src/myagent/agent/loop.py:181
  │     AgentRunSpec(messages, tools, model, 三个上限)
  │     └ AgentRunner.run()                      src/myagent/agent/runner.py:90
  │          for iteration in range(max_iterations):
  │            ① 取注入消息（injection_callback，V1 未启用）
  │            ② self.model.generate(messages, tools=definitions)   ← 契约调用
  │            ③ 没有 tool_calls → 完成（空回复最多重试 2 次）
  │            ④ 有 tool_calls → 按「只读可并发」分批执行
  │                 ToolRegistry.execute() → ToolResult
  │                 超长截断 / 错误回灌 → Message.tool(...)
  │            ⑤ 跑满上限 → 补一次「不带工具」的收尾请求
  │
  ├─[save]    _save_turn()                       src/myagent/agent/loop.py:197
  │     只追加本轮新增消息（系统提示永不落盘）→ data/sessions/cli%3Adefault.jsonl
  │
  └─[respond] _prepare_outbound()                src/myagent/agent/loop.py:218
        AgentRunResult → OutboundMessage(content, stop_reason, tools_used)
        → run_once() 返回文本 / run() 投递到 bus
```

关键点：

- **四个阶段各自计时并打日志**（`src/myagent/agent/loop.py:238`，对应上游 `agent/loop.py:1689`）。
  一条 `MYAGENT_LOG_LEVEL=DEBUG` 的运行能看到 `build 0.2ms / run 4252.7ms / save 1.3ms / respond 0.0ms`。
- **只有「本轮新增」的消息写盘**：`ContextBundle.transcript_start`
  （`src/myagent/agent/context.py:124`）指向本轮的 user 消息，它之前的系统块（每轮重建）与已存历史
  都不再重复落盘——`_save_turn`（`src/myagent/agent/loop.py:212`）只追加
  `messages[transcript_start:]` 加 runner 新产出的消息。
- **会话内串行、跨会话并发**：`_session_lock()`（`src/myagent/agent/loop.py:247`）按 session_key 缓存
  `asyncio.Lock`，与上游同一思路（`agent/loop.py:1391`、`agent/loop.py:2382`）。
- **预算不足的一轮不发请求**：`build` 阶段捕获 `ContextBudgetExceeded`
  （`src/myagent/agent/loop.py:174`）写进 `TurnContext.error`，`run` / `save` 直接返回
  （`:183`、`:208`），`respond` 给出一句 `The request was not sent: ...`（`:223`）。
  回归测试：`tests/test_loop.py:245`。

## 3. 模块契约

### 3.1 模块职责与「不做什么」（PLAN 3.1）

对照上游确定边界（`agent/loop.py:196` 的 `AgentLoop`、`agent/context.py:89` 的 `ContextBuilder`）：

| 模块 | 负责 | 明确不负责 |
| --- | --- | --- |
| `AgentLoop` | 消息接收、会话内串行、turn 阶段编排、落盘、出站 | 拼 prompt、调模型、检索记忆/文档 |
| `AgentRunner` | 模型 ↔ 工具循环、终止条件、错误回灌 | 上下文预算、会话持久化 |
| `ContextManager` | 按优先级/预算组装 messages、结构修复、压缩 | 决定「检索什么」（它只消费检索结果） |
| `ModelProvider` | LLM 调用与响应归一化 | 工具执行、prompt 组装 |
| `ToolRegistry` | 工具注册、schema、参数校验、并发执行 | 决定什么时候调用工具 |
| `SessionStore` | 转录读写、摘要检查点 | 提炼长期记忆 |
| `MemoryManager` | 分层记忆的写入/检索/巩固 | 拼 system prompt |
| `RagPipeline` | ingest 与检索，返回带引用的 chunk | 决定把哪些 chunk 塞进 context |

上游 Loop 之所以「重」，是因为它同时承担了频道投递、cron、子 agent、崩溃恢复
（`agent/loop.py:1594` 的 7 阶段流水线）；我们的 Loop 只保留其中的 `build / run / save / respond`
（`agent/loop.py:1865` 组装上下文的原料、`agent/loop.py:2014` 落盘）。

代码上怎么保证「不负责」不是靠自觉：Loop 的构造函数只接受契约类型
（`src/myagent/agent/loop.py:120`），而「核心模块不 import 具体实现 / SDK」由
`tests/test_contracts.py:435`、`:442` 的 AST 检查兜底。

### 3.2 六个扩展点（PLAN 3.4）

六个扩展点，全部用 `typing.Protocol`（结构类型），实现方无需继承：

| 接口 | 位置 | 最小方法签名 | 实现者 |
| --- | --- | --- | --- |
| `BaseModel` | `src/myagent/models/base.py:71` | `generate(messages, tools) -> LLMResponse` | `OpenAICompatModel` |
| `BaseTool` | `src/myagent/tools/base.py:152` | `execute(**kwargs) -> ToolResult` | 内置工具（上游契约参照 `agent/tools/base.py:159`） |
| `BaseMemory` | `src/myagent/memory/base.py:88` | `add(record)` / `search(query, kind, top_k)` | Phase 4 的分层实现 |
| `BaseEmbedder` | `src/myagent/rag/embedder.py:15` | `embed(texts) -> list[list[float]]` | `DashScopeEmbedder`（Phase 5） |
| `BaseVectorStore` | `src/myagent/rag/vectorstore.py:19` | `upsert(...)` / `search(vector, top_k, filters)` | `QdrantVectorStore`（Phase 5） |
| `BaseRetriever` | `src/myagent/rag/retriever.py:19` | `retrieve(query, top_k, filters) -> list[RetrievedChunk]` | `VectorRetriever`（Phase 5） |

加上 Loop 自己还要的两个边界（同一条决策，一并记在 ADR-0007）：

| 接口 | 位置 | 最小方法签名 | 实现者 |
| --- | --- | --- | --- |
| `ContextManager` | `src/myagent/agent/context.py:166` | `build(request) -> ContextBundle` / `compact(history) -> CompactionReport` | `SectionedContextManager` |
| `SessionStore` | `src/myagent/session/base.py:44` | `get_or_create` / `append` / `clear` / `known_keys` | `JsonlSessionStore` |

签名如下（省略文档字符串）：

```python
class BaseModel(Protocol):                     # src/myagent/models/base.py:71
    async def generate(self, messages, *, tools=None) -> LLMResponse: ...
    def stream(self, messages, *, tools=None) -> AsyncIterator[str]: ...   # Phase 6 用
    def count_tokens(self, messages, tools=None) -> int | None: ...        # Phase 6 用

class BaseTool(Protocol):                      # src/myagent/tools/base.py:152
    name: str
    description: str
    read_only: bool
    exclusive: bool
    @property
    def parameters(self) -> dict[str, Any]: ...
    @property
    def concurrency_safe(self) -> bool: ...
    async def execute(self, **kwargs: Any) -> ToolResult: ...
    def to_schema(self) -> dict[str, Any]: ...
    def cast_params(self, params: dict[str, Any]) -> dict[str, Any]: ...
    def validate_params(self, params: Any) -> list[str]: ...

class BaseMemory(Protocol):                    # src/myagent/memory/base.py:88
    def add(self, record: MemoryRecord) -> MemoryRecord: ...
    def search(self, query: str, *, kind: str | None = None, top_k: int = 5) -> list[MemoryRecord]: ...
    def all(self) -> list[MemoryRecord]: ...
    def clear(self) -> None: ...

class BaseEmbedder(Protocol):                  # src/myagent/rag/embedder.py:15
    async def embed(self, texts: list[str]) -> list[list[float]]: ...
    @property
    def dim(self) -> int: ...

class BaseVectorStore(Protocol):               # src/myagent/rag/vectorstore.py:19
    def ensure_collection(self, dim: int) -> None: ...
    def upsert(self, chunks: list[Chunk], vectors: list[list[float]]) -> None: ...
    def search(self, vector: list[float], top_k: int, filters: Filter | None = None) -> list[ScoredPoint]: ...
    def delete_document(self, document_id: str) -> None: ...

class BaseRetriever(Protocol):                 # src/myagent/rag/retriever.py:19
    async def retrieve(
        self, query: str, top_k: int = 5, *, document_ids: list[str] | None = None
    ) -> list[RetrievedChunk]: ...
```

两个刻意的设计选择：

- **契约就近定义在各模块**，不设汇总的 `protocols.py`：汇总文件会成为新的共享中心，
  而且会把「接口语义」搬得离实现很远（`BaseRetriever` 为什么把 `score` 写进契约，
  理由在 `src/myagent/rag/retriever.py:1` 的模块注释里——Phase 8 的 `hit@k` 要用它）。
- **ABC 没有被禁用，而是降级为便利实现**：`BaseTool` 是契约，`Tool(ABC)`
  （`src/myagent/tools/base.py:187`）是实现该契约的可选基类，内置工具继承它省掉
  schema / 类型纠正 / 校验的样板；不继承也完全可用——`tests/test_contracts.py:70` 的
  `DuckTool` 什么都不继承，registry 与 runner 照常驱动它（`tests/test_contracts.py:305`）。

完整决策（为什么不是 `isinstance` 分支、不是 ABC、不是 DI 容器）见
`docs/decision-records/0007-framework-extension-points.md`。

### 3.3 agent/types.py：一个消息表示，三个消费方

`Message`（`src/myagent/agent/types.py:100`）同时是「给模型的请求」「模型的回答」「落盘的转录」。
上游有三套并行表示（dataclass、provider 请求 dict、JSONL 记录），字段容易在层之间丢失；
V2 只保留一套 dataclass + 两个显式转换器：

```text
Message ──to_openai_messages()──▶ OpenAI 请求体        （src/myagent/models/openai_compat.py:47）
Message ──to_dict()/from_dict()─▶ JSONL 记录与回放     （src/myagent/agent/types.py:155、src/myagent/agent/types.py:136）
```

角色只有四种：`system` / `user` / `assistant`（可带 `tool_calls`）/ `tool`（带 `tool_call_id`）。
构造用 `Message.system/user/assistant/tool`（`src/myagent/agent/types.py:114`），避免手写 role 字符串。

`StopReason`（`src/myagent/agent/types.py:90`）用 `StrEnum`，取值就是 PLAN 2.4 要求的四个：
`completed` / `max_iterations` / `error` / `empty_final_response`；用枚举而不是裸字符串，
是为了让「终止原因」在类型层面可检查，同时因为它继承 `str`，写日志/落盘仍然直白。

### 3.4 models/：一个契约 + 一个实现

```python
class BaseModel(Protocol):                        # src/myagent/models/base.py:71
    async def generate(...) -> LLMResponse      # src/myagent/models/base.py:73
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
| 异常→框架错误集中翻译 | `src/myagent/models/openai_compat.py:128` | 超时/连接/鉴权/限流/上下文超限/其它 HTTP 错误各有独立文案 |
| 认证类失败也识别为「上下文超限」 | `src/myagent/models/openai_compat.py:147` | 提供方既可能给 `error.code`，也可能只在文案里写 "maximum context length" |
| 暴露只读 `settings` | `src/myagent/models/openai_compat.py:75` | 装配点建好的模型可以被检查（`tests/test_contracts.py:387` 用它证明「换模型只改 `.env`」） |

### 3.5 tools/：契约 → ABC → 校验 → 执行

```text
ToolResult(str)            src/myagent/tools/base.py:41    str 子类 + is_error，工具失败不抛异常
BaseTool(Protocol)         src/myagent/tools/base.py:152   registry/runner/模型只认这个形状
Tool(ABC)                  src/myagent/tools/base.py:187  可选基类：name/description/parameters/read_only/exclusive
 ├ to_schema()             :220                 → OpenAI function 定义
 ├ cast_params()           :231                 "3" → 3（按 schema 纠正类型）
 ├ validate_params()       :238                 → 人类可读的错误列表
 └ concurrency_safe        :211                 read_only and not exclusive
validate_schema_value()    :122                 JSON Schema 子集的校验实现
```

`ToolRegistry`（`src/myagent/tools/registry.py:26`）存的是 `BaseTool`，必须做到四件事：

| 能力 | 位置 | 行为 |
| --- | --- | --- |
| `register` / `get` / `tool_names` | `:33`、`:43`、`:52` | 显式注册，不自动发现 |
| `get_definitions` | `:56` | **按名字排序**，让工具块在 prompt 缓存下保持稳定（上游 `agent/tools/registry.py:86`） |
| `prepare_call` | `:64` | 名字纠错（`DEMO` → `demo`）+ 类型纠正 + schema 校验，**任何情况都不抛异常**，错误以文本返回 |
| `execute` | `:106` | 兜底入口：内部异常 → `ToolResult.error`，并追加 `[Analyze the error above and try a different approach.]`（`src/myagent/tools/registry.py:153`，与上游 `agent/tools/execution.py:_RETRY_HINT` 同句） |

四个内置工具（`src/myagent/tools/builtin/__init__.py:30` 显式注册）：

| 工具 | 只读 | 参数 | 说明 |
| --- | --- | --- | --- |
| `calculator` | ✅ | `expression` | AST 白名单求值，**不用 `eval`**；限制指数 ≤ 64，除零/溢出转成工具错误（`src/myagent/tools/builtin/calculator.py:52`） |
| `current_time` | ✅ | `timezone`(可选) | `zoneinfo` 时区名，默认本地（`src/myagent/tools/builtin/current_time.py:37`） |
| `read_file` | ✅ | `path`, `max_lines` | 工作区限定 + 行号 + 截断提示（`src/myagent/tools/builtin/read_file.py:48`） |
| `search_local` | ✅ | `query`, `limit` | 受控本地检索桩：字面量、大小写不敏感、跳过隐藏目录/超大文件；**Phase 7 换成真实检索**（`src/myagent/tools/builtin/search_local.py:60`） |

工作区边界由 `resolve_in_workspace()` 统一实现（`src/myagent/tools/builtin/paths.py:18`）：
拒绝绝对路径，解析符号链接后必须仍在工作区内——这是上游 `security/workspace_access.py` 的 V1 版本。

**新增工具的成本**（PLAN 3 验收标准）：写一个满足 `BaseTool` 形状的类 →
`registry.register(instance)`，runner 与 loop 一行不改。测试见
`tests/test_contracts.py:322`（断言新工具的 schema 原样到达提供方）。

### 3.6 agent/runner.py：模型 ↔ 工具循环

```python
@dataclass(slots=True)
class AgentRunSpec:            # src/myagent/agent/runner.py:58
    messages: list[Message]
    tools: ToolRegistry
    model: BaseModel                 # 契约，不是 OpenAICompatModel
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

Runner 与具体提供方无关：它的 import 里没有 `openai`，也没有 `myagent.models.openai_compat`
（`tests/test_contracts.py:435`、`:442`），所以 `tests/test_contracts.py:305` 可以用一个不继承
任何东西的假工具 + 假模型跑完整条链路。

**本阶段明确不做**（PLAN 2.4）：长度续写、畸形 tool_calls 重试、provider 原生状态、三阶段 checkpoint 恢复。

### 3.7 agent/loop.py：4 阶段 + 锁 + 最小 Bus

构造签名（PLAN 3.2 的「不 new 任何具体对象」，`src/myagent/agent/loop.py:120`）：

```python
class AgentLoop:
    def __init__(
        self,
        *,
        model: BaseModel,                  # 契约，而不是 OpenAICompatModel
        tools: ToolRegistry,
        context: ContextManager,           # 契约
        sessions: SessionStore,            # 契约
        runtime: AgentRuntimeConfig,
        bus: MessageBus | None = None,
    ) -> None: ...
```

| 阶段 | 位置 | 做什么 |
| --- | --- | --- |
| `build` | `src/myagent/agent/loop.py:162` | 取会话（`get_or_create`）+ 构造 `ContextRequest` + `context.build()` |
| `run` | `src/myagent/agent/loop.py:181` | 构造 `AgentRunSpec` → `AgentRunner.run()` |
| `save` | `src/myagent/agent/loop.py:197` | 只追加**本轮新增**消息（依赖 `transcript_start`）|
| `respond` | `src/myagent/agent/loop.py:218` | `AgentRunResult` → `OutboundMessage`（含 stop_reason / tools_used） |

与上游 7 阶段（`agent/loop.py:1594`）的差别：`restore`（崩溃恢复）与 `compact`（空闲压缩）
合并进 `build`，并且 V1 没有 `command` 阶段——`/exit`、`/session`、`/clear` 由 CLI 自己处理
（上游把命令放在模型调用之前的原因是一样的：命令必须在模型不可用时也能生效）。

`MessageBus`（`src/myagent/agent/loop.py:52`）是 Phase 2 的最小实现：两个 `asyncio.Queue`，
`run()`（`src/myagent/agent/loop.py:144`）消费到 `None` 哨兵就返回。上游的 Bus 要负责
5 种频道、cron、子 agent 的扇出，V1 只需要「一条进、一条出」。

**失败的一轮也走完 4 个阶段**（这是有意的）：模型调用失败时 runner 返回
`stop_reason="error"` 而不是抛异常（`src/myagent/agent/runner.py:104`），于是 `save` 仍会写下
这条用户消息，`respond` 给出一句可读的 `The model call failed: ...`
（`src/myagent/agent/loop.py:260`）。代价是历史里会留下一条**没有回答的用户消息**——
下一轮请求因此可能出现连续两条 user 消息（OpenAI 兼容端点接受），换来的是「用户说过什么」
不会因为一次网络抖动而丢失。Phase 9 若要做重试/补偿，应该在这一层加。

注意区分两种失败（Phase 3 新增的第二种）：

| 失败 | `TurnContext.error` | 是否落盘 | 出口文案 |
| --- | --- | --- | --- |
| 请求没发出去（超预算） | 有 | **不落盘**（请求从未发出） | `The request was not sent: ...`（`src/myagent/agent/loop.py:223`） |
| 请求发出但模型失败 | 无（走 runner 的 `stop_reason=error`） | 落盘（用户消息 + 无回答） | `The model call failed: ...`（`src/myagent/agent/loop.py:260`） |

### 3.8 agent/context.py：从「拼字符串」到「可裁剪的 section」

Phase 2 的 `ContextBuilder` 用字符串拼接组装一次请求；Phase 3 换成 PLAN 3.3 的 section 模型：
请求的每一部分都是一个 `ContextSection`，自带 `priority` / `required` / `budget_tokens`，
这样 Phase 6 可以按优先级裁剪并汇报「裁掉了什么」，而不是把决定藏进 f-string。

```python
@dataclass(frozen=True, slots=True)
class ContextSection:      # src/myagent/agent/context.py:89
    name: str                     # system / conversation / memory / rag / tools
    priority: int                 # 越小越先保留（PLAN 6.1）
    required: bool                # 不可裁剪
    content: str | list[Message]
    budget_tokens: int | None = None
    def estimated_tokens(self) -> int: ...        # :98

@dataclass(frozen=True, slots=True)
class ContextRequest:      # src/myagent/agent/context.py:106
    user_input: str
    history: Sequence[Message] = ()
    memories: Sequence[ContextItem] = ()          # Phase 4 填
    rag_chunks: Sequence[ContextItem] = ()        # Phase 5 填
    tools: Sequence[Mapping[str, Any]] = ()
    session_key: str = ""
    budget_tokens: int | None = None

@dataclass(frozen=True, slots=True)
class ContextBundle:       # src/myagent/agent/context.py:124
    messages: list[Message]
    transcript_start: int                 # 本轮新增消息的起点
    sections: tuple[ContextSection, ...] = ()
    estimated_tokens: int = 0
```

| 行为 | 位置 | 说明 |
| --- | --- | --- |
| 优先级常量 | `src/myagent/agent/context.py:55` | system 0 / conversation 2 / memory 4 / rag 5 / tools 6（PLAN 6.1 的表） |
| section 组装 | `src/myagent/agent/context.py:204` | system 恒在；memory / rag / tools 为空时**不占位** |
| 字符串 section 合并成一条 system | `src/myagent/agent/context.py:241` | 与上游把记忆与检索结果拼进 system 的做法一致（`docs/memory.md` §4） |
| 超预算报错 | `src/myagent/agent/context.py:246` | `ContextBudgetExceeded(estimated, budget)`（:148）——V1 **只报不裁**，裁剪是 Phase 6 |
| 压缩 | `src/myagent/agent/context.py:256` | `compact()` 返回空 `CompactionReport`（:140），Phase 6 填实现 |

检索结果通过 `ContextItem(text, reference, score)`（`src/myagent/agent/context.py:74`）
进入 context：memory / rag 只交「文本 + 出处」，不交自己的记录类型。
原因是 `agent` 不能依赖 `memory` / `rag`（§1 的第 1 条约束），
而且 Phase 4/5 改内部表示时不必动这个契约。

### 3.9 session/：契约与实现分家

Phase 2 把 `Session` 与 JSONL 实现放在一个模块；Phase 3 拆成两半：

- **契约** `src/myagent/session/base.py:44` 的 `SessionStore`（`get_or_create` / `append` /
  `clear` / `known_keys`）+ `Session`（`src/myagent/session/base.py:30`，
  `transcript()` 从 `last_archived` 开始返回，`:38`）；
- **实现** `JsonlSessionStore`（`src/myagent/session/manager.py:41`）。

```text
data/sessions/cli%3Adefault.jsonl
├─ {"type":"session","key":"cli:default","created_at":"...","last_archived":0}   ← 头记录
├─ {"type":"message","message":{"role":"user","content":"..."}}
├─ {"type":"message","message":{"role":"assistant","tool_calls":[...]}}
└─ {"type":"message","message":{"role":"tool","tool_call_id":"...","content":"..."}}
```

- 一个 session 一个文件，文件名是 `quote(key, safe="")`
  （`src/myagent/session/manager.py:58`），`cli:default` → `cli%3Adefault.jsonl`，
  因此 `a/b` 与 `a-b` 不会撞车。
- **只追加**，不重写：崩溃最多丢掉尾部几条，不会破坏已有历史（上游 `session/manager.py:548`
  的 `JsonlSessionStore` 同样以追加为主；实现见 `src/myagent/session/manager.py:115`）。
- `last_archived` 是**为 Phase 4/6 预留**的边界字段：压缩时前移即可，不删消息。
- 坏行不致命：无法解析的行打 warning 后跳过（`src/myagent/session/manager.py:93`）。
- 把 JSONL 换成 SQLite 只需要新的 `SessionStore` 实现 + 改 `build_agent` 的默认值。

### 3.10 memory/：接口先立住，Phase 4 接线

Phase 3 按 PLAN 3.4 的表改了两处 Phase 2 的接口：`add(record)` 而不是 `add(content)`
（id / kind / importance 由上层决定，store 只负责持久化），
`search(query, kind=..., top_k=...)` 而不是 `search(query, limit=...)`（分层记忆按 kind 检索）。

```python
MemoryRecord               # src/myagent/memory/base.py:34
 ├ id / text / created_at
 ├ kind: str = "episodic"           # EPISODIC / SEMANTIC（:29、:30），Phase 4 收紧为 Literal
 ├ tags: tuple[str, ...]
 └ source: str | None
BaseMemory(Protocol)       # src/myagent/memory/base.py:88   add / search / all / clear
FileMemoryStore            # src/myagent/memory/store.py:21  「一个 JSONL + 字面量检索」的最小实现
```

**它没有接进 Loop**：检索策略、写入时机、三层模型（working / episodic / semantic）
都是 Phase 4 的设计，现在接进去只会制造 Phase 3 需要拆掉的耦合。
`ContextRequest.memories` 字段已经就位，Phase 4 只要填它。

### 3.11 rag/：Phase 5 的四个接口，本阶段只有类型

`src/myagent/rag/` 今天只有契约与数据类型，没有实现，也没有任何第三方依赖：

| 文件 | 内容 | Phase 5 的默认实现 |
| --- | --- | --- |
| `src/myagent/rag/types.py:26` | `Document` / `Chunk`（:37）/ `ScoredPoint`（:48）/ `RetrievedChunk`（:57）/ `Filter`（:22） | — |
| `src/myagent/rag/embedder.py:15` | `BaseEmbedder.embed` / `.dim` | `DashScopeEmbedder`（ADR-0005） |
| `src/myagent/rag/vectorstore.py:19` | `BaseVectorStore.ensure_collection` / `upsert` / `search` / `delete_document` | `QdrantVectorStore`（ADR-0003） |
| `src/myagent/rag/retriever.py:19` | `BaseRetriever.retrieve(query, top_k, document_ids=...)` | `VectorRetriever` |

`Filter` 是 `Mapping[str, Any]`（`src/myagent/rag/types.py:22`）而不是 Qdrant 的 `Filter` 类型：
这样契约文件不需要 import `qdrant_client`，「核心不 import SDK」的约束（§1）才守得住。
`RetrievedChunk.score` 与 `chunk.id` 写进契约的原因是 Phase 8 用这两个字段算 `hit@k`。

### 3.12 config 与 CLI

- **`Settings`（`src/myagent/config/settings.py:388`）是配置的唯一样本**：
  `llm`（`LLMSettings`，:258）/ `agent`（`AgentSettings`，:339）/ `sqlite`（:97）/
  `qdrant`（:111）/ `embedding`（:150），`Settings.from_env()`（:404）一次读完 `.env`。
- 组件**只接收 settings 对象，不读环境变量**：`OpenAICompatModel(settings.llm)`、
  `JsonlSessionStore.from_settings(settings.agent)`、
  `build_default_registry(settings.agent)`、`AgentRuntimeConfig.from_settings(agent, llm)`。
- `LLMSettings.from_env()` 只校验形状，**不要求** model/key 存在（:295）；
  `require_model()` / `require_api_key()`（:321、:327）在真正要发请求时才失败——
  这样 `myagent tools` 这类离线命令照常可用。
- `myagent chat -m "..."` / `myagent chat`（交互：`/exit`、`/session`、`/clear`）/
  `myagent tools`（`src/myagent/cli.py:34`），`myagent tools` 只读 `AgentSettings`
  并打印注册表内容（`src/myagent/cli.py:62`）。
- **CLI 不再装配**：`src/myagent/cli.py:83`、`:64` 都只调用 `build_agent()`。
  Phase 2 的「装配与命令行混在一个文件里」这个已知妥协到此结束。

### 3.13 装配：`build_agent` 是唯一的注入点（PLAN 3.5）

```python
# src/myagent/runtime.py:34
def build_agent(
    settings: Settings | None = None,
    *,
    model: BaseModel | None = None,
    tools: ToolRegistry | None = None,
    context: ContextManager | None = None,
    sessions: SessionStore | None = None,
    runtime: AgentRuntimeConfig | None = None,
    bus: MessageBus | None = None,
) -> AgentLoop: ...
```

```text
Settings（.env → Settings.from_env()）
   │
   ├── LLMSettings        → OpenAICompatModel           src/myagent/runtime.py:53
   ├── AgentSettings      → build_default_registry      （→ ToolRegistry）      :54
   │                      → SectionedContextManager                            :57
   │                      → JsonlSessionStore.from_settings                    :60
   │                      → AgentRuntimeConfig.from_settings                   :63
   ├── SQLiteSettings     →（Phase 4）MemoryStore
   ├── QdrantSettings     →（Phase 5）QdrantVectorStore
   └── EmbeddingSettings  →（Phase 5）DashScopeEmbedder
                              │
                              ▼
                    AgentLoop（model / tools / context / sessions / runtime / bus）
```

两条规则：

1. **默认值只从 `settings` 来**，`settings=None` 时才调用 `Settings.from_env()`
   （`src/myagent/runtime.py:51`）——所以「换模型只改 `.env`」不是口号，
   `tests/test_contracts.py:387` 用两份 `Settings` 装出两个模型、断言其余组件类型不变。
2. **每个 kwarg 覆盖一个组件**，这是测试注入假件的方式（`tests/test_contracts.py:363`），
   也是 Phase 4/5/6 接新实现的方式：改默认值的人只有这一个函数。

`AgentRuntimeConfig`（`src/myagent/agent/runtime.py:33`）把「一轮怎么跑」从配置加载里分出来：
`max_iterations` / `tool_timeout_s` / `max_tool_result_chars` /
`context_budget_tokens`（`from_settings`，:56）。
预算公式是 PLAN 6.2 的 `context_window - max_tokens - 1024`
（`src/myagent/agent/runtime.py:73`，`_SAFETY_MARGIN_TOKENS` 在 :29）；
窗口太小以至算不出正数时返回 `None`，表示「本阶段不做预算检查」
（`tests/test_runtime.py:87`）。
上游把这类上限放在配置层（`config/schema.py:129` 的 `max_tool_iterations`、
`max_tool_result_chars`），我们多走一步：配置层给默认值，运行期用一个小值对象传进 Loop。

## 4. 与上游 nanobot 的差异表

### 4.1 保留（同构迁移）

| 机制 | 上游 | myagent | 为什么保留 |
| --- | --- | --- | --- |
| Loop / Runner 分离 | `agent/loop.py:196` / `agent/runner.py:89` | `src/myagent/agent/loop.py:117` / `src/myagent/agent/runner.py:87` | 职责边界清晰，Runner 可单独测试 |
| 阶段化流水线 + 计时 | `agent/loop.py:1689` | `src/myagent/agent/loop.py:238` | 定位慢/坏的一轮不需要调试器 |
| 会话内串行、跨会话并发 | `agent/loop.py:1391` | `src/myagent/agent/loop.py:154`、`:247` | 正确性前提，成本几乎为零 |
| 工具结果「错误即观察」 | `agent/tools/execution.py:_with_retry_hint` | `src/myagent/tools/registry.py:153` | 模型能自我纠偏，一轮不会因工具失败而中断 |
| 只读工具并发分批 | `agent/tools/execution.py:292` | `src/myagent/agent/runner.py:220` | 实现便宜、收益明确（一次 3 个只读调用只花 1 个 RTT 批次） |
| 工具定义按名排序 | `agent/tools/registry.py:86` | `src/myagent/tools/registry.py:56` | prompt 缓存友好 |
| 会话 JSONL 追加式 | `session/manager.py:548` | `src/myagent/session/manager.py:115` | 崩溃不破坏历史 |
| schema 驱动的类型纠正 | `agent/tools/base.py:251` | `src/myagent/tools/base.py:231` | 模型给的 `"3"` 不该导致校验失败 |
| 上下文分层（system / 记忆 / 检索） | `agent/context.py:89` | `src/myagent/agent/context.py:204` | 决定「模型看到什么」的顺序不能随手改 |

### 4.2 简化

| 上游机制 | V1/V2 做法 | 理由 / 后续 |
| --- | --- | --- |
| 7 阶段（含 `restore`/`compact`/`command`） | 4 阶段，命令在 CLI | 崩溃恢复与压缩是 Phase 4/9；CLI 命令只需一个 `if` |
| `TurnContext` 20+ 字段 | 7 个字段（`src/myagent/agent/loop.py:87`） | 只保留本阶段真的会用的；`require_*()` 保证阶段顺序错误立刻暴露 |
| `LLMProvider` 广接口（状态/遥测/重试策略） | `BaseModel` 三个方法 | Phase 6 需要预算时才扩 |
| 工具自动发现 / 插件 / MCP | 显式注册 4 个工具 | PLAN 2.3 明确本阶段不做自动发现 |
| `Schema` 抽象基类 + 装饰器 | `BaseTool` Protocol + 可选 `Tool` ABC | 4 个工具不值得类改写（`agent/tools/base.py:318` 的 `tool_parameters`） |
| 完整 JSON Schema | 子集（type / enum / min·max / minLength·maxLength / required / additionalProperties / 嵌套） | 只实现内置工具用得到的部分，避免把 pydantic 拉进工具层 |
| 5 种频道投递（`TurnDelivery`） | `OutboundMessage` 直出 | 只有 CLI 与垂直 Agent |
| `ContextGovernor` + `ContextBuilder` 两套上下文机制 | 一个 `ContextManager`（section + 预算） | 上游先拼再拟合，我们要的是「一次组装、可裁剪」，且裁剪要能报告 |
| provider 原生状态、checkpoint | 不做 | PLAN 2.4 写明「本阶段不做」 |

### 4.3 相对 PLAN 的加法（都要在评审时能说清）

| 加法 | 位置 | 原因 |
| --- | --- | --- |
| `AgentRunResult.error` | `src/myagent/agent/runner.py:84` | 只有 `stop_reason="error"` 无法把失败原因告诉用户 |
| `ToolCallRequest.parse_error` | `src/myagent/agent/types.py:41` | 「参数解析失败要能被上层看见」（PLAN 2.2）需要一个载体 |
| `stop_reason` 用 `StopReason` 枚举 | `src/myagent/agent/types.py:90` | 是 `str` 子类，不违反 PLAN 的 `str` 约定，但可被类型检查 |
| `AgentHook` 协议 | `src/myagent/agent/runner.py:40` | 给 Phase 3 的流式/进度留口，V1 只有测试与 CLI 用 |
| `MessageBus` 放在 `loop.py` | `src/myagent/agent/loop.py:52` | PLAN 2.1 的文件清单没有 `bus.py`，V1 只有 30 行，Phase 3 再拆 |
| `tokens.py::estimate_tokens` | `src/myagent/tokens.py:37` | PLAN 3.3 要「token 估算」，Phase 5 的 chunk 元数据与 Phase 6 的预算需要同一把尺子 |

### 4.4 Phase 3 相对 Phase 2 的变化

| Phase 2 的状态 | Phase 3 的做法 | 证据 |
| --- | --- | --- |
| 注入具体类（`OpenAICompatModel` / `ContextBuilder` / `SessionManager`） | 注入契约（`BaseModel` / `ContextManager` / `SessionStore`） | `src/myagent/agent/loop.py:120` |
| 装配在 `cli.build_agent_loop()` | 装配在 `runtime.build_agent()`，CLI 只调用 | `src/myagent/runtime.py:34`、`src/myagent/cli.py:83` |
| `ContextBuilder` 拼字符串，无预算 | `SectionedContextManager`：section + 优先级 + token 估算 + 超预算报错 | `src/myagent/agent/context.py:89`、`:246` |
| `memory/` 用 `add(content)` / `search(query, limit=)` | `add(record)` / `search(query, kind=, top_k=)` | `src/myagent/memory/base.py:88`、`:95` |
| `Tool(ABC)` 是唯一契约 | `BaseTool` Protocol 是契约，`Tool(ABC)` 降为便利实现 | `src/myagent/tools/base.py:152`、`:187` |
| Loop 与 Context 的依赖在运行时无校验 | AST 检查核心模块的 import 边界 | `tests/test_contracts.py:435` |

## 5. 尚未实现（Phase 4+ 的输入）

| 未做 | 谁来做 | 现状 |
| --- | --- | --- |
| 长度续写、上下文预算裁剪、`count_tokens` 真实值 | Phase 6 | `count_tokens()` 返回 `None`（`src/myagent/models/openai_compat.py:119`）；`compact()` 是空实现（`src/myagent/agent/context.py:256`） |
| 超预算时的优先级裁剪（现在只报错） | Phase 6 | `ContextBudgetExceeded`（`src/myagent/agent/context.py:148`）会在 Phase 6 退化成「连必留 section 都放不下」时的兜底 |
| 结构修复（tool 消息顺序、孤儿 tool_call） | Phase 6 | 没有实现 |
| `last_archived` 前移与摘要检查点 | Phase 4/6 | 字段已落盘、语义已实现（`src/myagent/session/base.py:38`），没人前移它 |
| 三层记忆与检索 | Phase 4 | `memory/` 只有契约 + 文件实现（未接线） |
| RAG 实现（loader / chunker / embedder / store / retriever） | Phase 5 | `src/myagent/rag/` 只有契约与类型 |
| 真实论文检索 | Phase 7 | `search_local` 是受控桩（`src/myagent/tools/builtin/search_local.py:60`） |
| 崩溃恢复 / checkpoint | Phase 9 | 无 |
| `stream()` 与流式 CLI | Phase 6+ | 契约里有，实现抛 `NotImplementedError`；CLI 不流式 |
| 多会话管理（列表/切换） | Phase 3 之后按需 | `SessionStore.known_keys()`（`src/myagent/session/base.py:59`）已就位，CLI 只用默认会话 |

## 6. 答辩：四个设计问题

### 6.1 为什么 AgentLoop 不应该直接负责 RAG？

因为「检索」有三个独立的决策点，而 Loop 只应该负责其中一个的反面——**编排**：

1. **检索什么**（query 改写、top_k、过滤条件）依赖领域知识。上游的 `ContextBuilder`
   自己持有记忆存储（`agent/context.py:89` 的类、`:97` 的 `MemoryStore(workspace)`），
   于是「组装上下文」与「从哪里取记忆」被绑死在一起；我们把这一步交给
   `BaseRetriever` / `BaseMemory` 的实现（`src/myagent/rag/retriever.py:19`、
   `src/myagent/memory/base.py:88`），`ContextManager` 只消费检索结果；
2. **检索结果怎么进 prompt**（section 顺序、配额、超预算时先丢谁）是上下文策略，
   PLAN 6.1 给了优先级表，代码在 `src/myagent/agent/context.py:55`；
3. **一次请求要不要检索**（有没有文档可查、是否是闲聊）是编排决策，这才是 Loop 的份内事。

如果把 1/2 写进 Loop，会同时产生三个坏结果：Loop 必须 import 向量库客户端（破坏 §1 的
「核心不 import SDK」）、换 embedding 或换向量库要改 Loop（Phase 5 的全部工作）、
以及检索策略无法单独测试（今天 `ContextManager` 可以用 `CountingContextManager`
在不起网络的前提下被断言，`tests/test_contracts.py:220`）。

上游的教训正好是「Loop 什么都管」的代价：它同时承担频道投递、cron、子 agent、崩溃恢复
（`agent/loop.py:1594`），于是 7 阶段流水线里没有任何一段能被单独替换。
Loop 现在的边界由类型强制：构造函数只收契约（`src/myagent/agent/loop.py:120`），
而「核心不 import 具体实现」由 `tests/test_contracts.py:442` 检查。

### 6.2 为什么 Memory 和 RAG 要拆成独立模块，而不是合并成一个 Retrieval 模块？

因为两者的**写入者、生命周期和正确性判据完全不同**：

| 维度 | Memory（`src/myagent/memory/base.py:88`） | RAG（`src/myagent/rag/retriever.py:19`） |
| --- | --- | --- |
| 内容来源 | Agent 自己产生的结论、用户偏好（「发生过什么 / 我知道什么」） | 外部语料（论文、笔记），由 ingest 流程写入 |
| 谁写 | `MemoryManager`（Phase 4）在对话中判定后写 | Loader / Chunker（Phase 5），离线、幂等 |
| 变更方式 | 可被巩固/合并/遗忘（上游的 Dream，`docs/memory.md`） | 重新 ingest 一份文档就是 upsert + 删除旧 chunk |
| 正确性判据 | 「记错了」会误导后续所有对话 | 「检索错了」只是这一轮少给/给错引用，能从引用回溯 |
| 典型失败 | 记忆污染、过期事实 | 召回率（Phase 8 用 `hit@k` 度量） |

合并成一个模块的后果是：Phase 4 的记忆巩固逻辑与 Phase 5 的 ingest 幂等逻辑会挤在同一个类里，
`score` 的语义也要二选一（时间衰减分 vs 向量相似度），Phase 8 就无法分别度量两者。
上游没有这层区分——它只有一层长期记忆 `MEMORY.md`（`agent/memory.py:229` 读写、
`agent/memory.py:253` 注入上下文），这正是我们要补的洞。

对 Loop 来说两者却是同构的：都产出「文本 + 出处」，都通过同一个
`ContextItem`（`src/myagent/agent/context.py:74`）进入 `ContextRequest.memories` /
`.rag_chunks`。**接口相同、实现独立**，这才是拆分的价值。

### 6.3 为什么 Tool 需要 Registry，而不是一个 dict + 分支？

因为工具调用的输入是**模型生成的、不可信的 JSON**，一个 dict 只能回答「有没有这个工具」，
回答不了另外四个问题：

| 问题 | 今天的答案 | 位置 |
| --- | --- | --- |
| 名字对不对（`DEMO` vs `demo`，或拼错） | `prepare_call` 纠错后仍失败才报错 | `src/myagent/tools/registry.py:64`、`:126` |
| 参数类型对不对（模型给 `"3"`） | schema 驱动的类型纠正 | `src/myagent/tools/base.py:231` |
| 参数满足 schema 吗 | 结构化校验，错误以文本返回（不抛异常） | `src/myagent/tools/base.py:238` |
| 这次调用能不能和别的调用并发 | `concurrency_safe` 分批 | `src/myagent/agent/runner.py:220` |
| 工具内部炸了怎么办 | 统一兜底 + retry hint | `src/myagent/tools/registry.py:106`、`:153` |

再加两点工程理由：**顺序稳定**（`get_definitions` 按名字排序，`src/myagent/tools/registry.py:56`，
对 prompt 缓存友好）和**唯一入口**（`register`，`src/myagent/tools/registry.py:33`）——
「新增一个工具只需 `registry.register(...)`」这条验收标准就是它的直接推论
（`tests/test_contracts.py:322`）。

dict + 分支的写法会把上面五件事散到调用点，每加一个工具都要回到 runner 改 `if`，
而且测试只能端到端测——上游的 `agent/tools/registry.py` 与
`agent/tools/execution.py` 也是同样的分工，只是它把执行策略放在单独文件里。

### 6.4 为什么用 Protocol 而不是 ABC？

**先说结论**：契约用 `Protocol`，**便利实现仍然可以用 ABC**——`BaseTool` 是 Protocol，
`Tool(ABC)` 是它的可选基类（`src/myagent/tools/base.py:152`、`:187`），二者不是二选一。

选 Protocol 的理由：

1. **实现方不必继承，也不必 import 我们**：Phase 5 的 Qdrant 包装、Phase 4 的记忆后端
   可以是对上游 `Protocol`、甚至第三方库原生对象的一层薄壳；继承 ABC 则要求这些对象改成
   我们的子类（`tests/test_contracts.py:266` 断言假件 `__mro__` 里只有 `object`）。
2. **依赖方向不会因为继承被重新绑上**：ABC 会把 `agent` 与 `models` 变成父子关系，
   而 `Protocol` 只描述形状，`agent` 依然只 import 契约文件（§1 的三条约束）。
3. **替换实现零改动**：换模型只改 `.env`（`tests/test_contracts.py:387`）；
   加工具只 `register`（`tests/test_contracts.py:322`）。
4. **`isinstance` 分支被明确排除**：核心代码里一旦出现 `if isinstance(x, QdrantVectorStore)`，
   每加一个实现就要改核心，且核心必须 import 那个实现——正是 Phase 3 要拆掉的耦合。

代价也要说清楚（写在 ADR-0007 的「后果」里）：`runtime_checkable` 的 `isinstance`
**只检查成员是否存在，不检查签名**。所以 `ScriptedModel` 只有 `generate`，就**不**满足
`BaseModel`（`tests/test_contracts.py:274` 有意断言这一点）；带数据成员的 Protocol 上
`issubclass` 会抛 `TypeError`（`tests/test_contracts.py:266`）。
结论是：契约的成员一旦变动，所有假件都会立刻失败——这是有意的摩擦，不是缺陷。

## 7. 速查索引

| 主题 | 位置 |
| --- | --- |
| 模块职责与「不做什么」 | 本文 §3.1 |
| 八个契约 | `src/myagent/models/base.py:71`、`src/myagent/tools/base.py:152`、`src/myagent/memory/base.py:88`、`src/myagent/rag/embedder.py:15`、`src/myagent/rag/vectorstore.py:19`、`src/myagent/rag/retriever.py:19`、`src/myagent/agent/context.py:166`、`src/myagent/session/base.py:44` |
| 唯一装配点 | `src/myagent/runtime.py:34` |
| 一轮的行为上限 | `src/myagent/agent/runtime.py:33` |
| Loop 4 阶段 / 锁 / 失败语义 | `src/myagent/agent/loop.py:152`、`:247`、`:174` |
| ContextManager / section / 预算 | `src/myagent/agent/context.py:166`、`:89`、`:246` |
| Runner 主循环 / 收尾 / 截断 / 分批 | `src/myagent/agent/runner.py:90`、`:130`、`:213`、`:220` |
| 工具注册表（准备/执行/定义） | `src/myagent/tools/registry.py:64`、`:106`、`:56` |
| 内置工具注册 | `src/myagent/tools/builtin/__init__.py:30` |
| 会话契约 / JSONL 实现 | `src/myagent/session/base.py:44`、`src/myagent/session/manager.py:41` |
| 记忆契约（未接线） | `src/myagent/memory/base.py:88` |
| 检索契约（未接线） | `src/myagent/rag/retriever.py:19` |
| token 估算 | `src/myagent/tokens.py:37` |
| 设置项 | `src/myagent/config/settings.py:258`、`:339`、`:388` |
| CLI 入口 | `src/myagent/cli.py:34` |
| 契约与边界的测试 | `tests/test_contracts.py:252`、`:305`、`:363`、`:435` |
| 扩展点决策（ADR） | `docs/decision-records/0007-framework-extension-points.md` |
