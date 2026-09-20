# 基于 nanobot 二次开发的个人 Agent Framework 项目计划

## 1. 项目概述

### 1.1 项目目标

基于开源 Agent 框架 **nanobot** 的核心设计与部分实现，完成一次面向个人项目的二次开发与架构重构，最终形成一个具有独立代码结构和核心抽象的轻量级 Agent Framework。

在此 Framework 基础上，进一步开发一个垂直领域 Agent 应用，用于验证 Framework 在实际场景中的可扩展性。

项目最终形成两个层次：

```text
┌─────────────────────────────────────┐
│        Vertical Agent Application   │
│        垂直领域 Agent 应用            │
└──────────────────┬──────────────────┘
                   │
                   ▼
┌─────────────────────────────────────┐
│        Personal Agent Framework     │
│                                     │
│ Agent Loop                          │
│ Context Manager                     │
│ Memory                              │
│ RAG                                 │
│ Tool System                         │
│ Session                             │
│ Model Provider                      │
└──────────────────┬──────────────────┘
                   │
                   ▼
              LLM / Vector DB
```

### 1.2 项目核心目标

重点不在于实现大量功能，而在于：

1. 理解 Agent Framework 的核心运行机制。
2. 将 nanobot 的核心 Agent Runtime 进行重新组织和抽象。
3. 构建自己的 Memory 抽象与长期记忆机制。
4. 构建可插拔的 RAG Pipeline。
5. 对 Context 构建机制进行重构。
6. 通过一个垂直领域 Agent 验证 Framework。
7. 建立简单的 Evaluation Pipeline。
8. 最终形成能够写入简历、能够进行技术面试讲解的完整项目。

---

# 2. 项目范围

## 2.1 第一阶段重点

本项目第一阶段只关注以下核心能力：

```text
Agent Runtime
├── Agent Loop
├── Agent Runner
├── Context Manager
├── Session Manager
├── Tool Registry
└── Model Provider

Memory
├── Working Memory
├── Episodic Memory
├── Semantic Memory
└── Memory Retriever

RAG
├── Document Loader
├── Chunker
├── Embedding
├── Vector Store
├── Retriever
└── Reranker（可选）

Application
└── Vertical Agent
```

## 2.2 暂不重点实现

以下能力暂不作为项目核心：

* Multi-Agent
* MCP 全面支持
* Browser Agent
* Complex Workflow
* Autonomous Planning
* Reflection Agent
* Voice
* 多模态 Agent
* 分布式 Agent Runtime
* 复杂 Web UI
* 大规模生产部署

原则：

> 优先保证核心 Agent Runtime、Memory、RAG 和垂直应用的完整性，而不是堆积大量 Agent Feature。

---

# 3. 总体开发路线

```text
Phase 0
项目准备
    ↓
Phase 1
nanobot 源码理解
    ↓
Phase 2
核心代码迁移
    ↓
Phase 3
Agent Framework 重构
    ↓
Phase 4
Memory 系统改造
    ↓
Phase 5
RAG 系统建设
    ↓
Phase 6
Context 系统重构
    ↓
Phase 7
垂直领域 Agent
    ↓
Phase 8
Evaluation
    ↓
Phase 9
工程化
    ↓
Phase 10
README / Demo / 简历
```

建议周期：

**6～8 周**

如果时间有限，可以压缩到 4～6 周。

---

# Phase 0：项目准备

## 目标

建立开发环境，跑通 nanobot，并建立自己的项目仓库。

## 任务

### 0.1 获取并运行 nanobot

* [x] Clone nanobot
* [x] 安装依赖
* [x] 配置 LLM
* [x] 成功启动 CLI
* [x] 完成一次基础对话
* [x] 完成一次 Tool Calling
* [x] 测试 Memory
* [x] 测试 Session

### 0.2 建立自己的项目

创建独立 Repository：

```text
my-agent-framework/
```

初始目录：

```text
my-agent-framework/
├── README.md
├── PLAN.md
├── pyproject.toml
├── src/
├── tests/
└── docs/
```

结果（2026-09-17）：独立仓库为 `kyobot`（`origin https://github.com/Kyouko5/kyobot.git`），
仓库根目录即工程根，未再嵌套 `my-agent-framework/`（理由见 `docs/decision-records/0001`）。

```text
kyobot/
├── README.md          ✓
├── PLAN.md            ✓
├── pyproject.toml     ✓  （hatchling + ruff/mypy/pytest/coverage 配置）
├── .gitignore         ✓  （排除上游参照 nanobot/、.venv/、缓存与密钥）
├── .editorconfig      ✓
├── .pre-commit-config.yaml ✓
├── src/myagent/       ✓  （含 observability/logging.py 与 py.typed）
├── tests/             ✓  （24 项测试，覆盖率 100%）
├── docs/              ✓  （architecture / development / decision-records / records）
├── scripts/           ✓  （bootstrap.sh / check.sh）
└── nanobot/           ✓  （上游只读参照，git ignored）
```

### 0.3 建立开发规范

* [x] Python 项目规范（`requires-python >= 3.11`；src layout；命名与依赖管理约定，见 `docs/development.md`）
* [x] Git Commit 规范（Conventional Commits + `phase-<N>/<slug>` 分支 + 一个 Task 至少一次提交）
* [x] pytest（`tests/`，`asyncio_mode = "auto"`，`pythonpath = ["src"]`，24 项测试通过）
* [x] Ruff / Black（统一用 ruff：`ruff check` 规则集 `E,F,W,I,N,UP,B,C4,SIM,RUF,ASYNC,ANN` + `ruff format`，行宽 100）
* [x] 类型标注（`py.typed` + `mypy --strict` 覆盖 `src/`）
* [x] 基础 Logging（`src/myagent/observability/logging.py`：命名空间隔离、text/JSON 两种格式、结构化 `extra` 字段）

## 阶段产出

```text
☑ nanobot 可以正常运行          （v0.3.5，`nanobot agent` 交互与单条消息均可用）
☑ 自己的 GitHub Repository      （Kyouko5/kyobot）
☑ 开发环境                      （.venv + ruff/mypy/pytest/pre-commit，scripts/bootstrap.sh 可复现）
☑ 初始 README                   （README.md：定位 / 进度 / 快速开始 / 目录 / 文档索引）
☑ PLAN.md                       （本文件）
☑ 质量门                        （scripts/check.sh：format → lint → type → test + coverage）
```

## 验收标准

能够回答：

> nanobot 如何启动？

> 一条用户消息进入系统后，经过哪些模块？

结论（答案与文件行号见 `docs/architecture.md`）：

1. `nanobot` 命令由 `nanobot.cli.entry:main` 进入并路由到 agent 命令；`cli/agent.py` 依次装配
   config → provider → workspace 模板 → MessageBus → cron → ToolRegistry/MCP → `AgentLoop`，
   之后要么 `process_direct()` 单次执行，要么 `agent_loop.run()` 常驻消费总线。
2. 用户输入 → `InboundMessage` → `MessageBus.inbound` → `AgentLoop`（会话解析、`ContextBuilder` 组装上下文）
   → `AgentRunner` 的「模型 ↔ 工具」循环 → 写回 `SessionManager`（JSONL）与长期 Memory
   → `OutboundMessage` / 流式事件 → `MessageBus.outbound` → 频道渲染。

记录：`docs/records/phase-0-setup.md`

---

# Phase 1：nanobot 源码理解

## 目标

彻底理解 nanobot 的核心 Agent Runtime，而不是直接开始修改代码。

## 重点模块

```text
agent/
├── loop.py
├── runner.py
├── context.py
├── memory.py
└── tools/

session/
bus/
providers/
config/
```

## 任务

### 1.1 理解消息流

结果（2026-09-20）：消息流图与逐步说明见 `docs/architecture.md` 第 3 节（含每一步的文件行号）。
补充了计划书图里没有展开的部分：pending queue 注入、会话内串行/跨会话并发、save 阶段的摘要检查点写入。

画出：

```text
User
 ↓
Message
 ↓
MessageBus
 ↓
AgentLoop
 ↓
AgentRunner
 ↓
LLM
 ↓
Tool
 ↓
Tool Result
 ↓
LLM
 ↓
Final Response
```

### 1.2 理解 AgentLoop

重点分析：

* [x] AgentLoop 的职责（`agent-loop.md` 1.0 / 1.1）
* [x] 消息接收方式（`run()` 循环 + 命令直通 + pending queue，`agent-loop.md` 1.2）
* [x] Session 如何管理（`agent-loop.md` 1.5，key / JSONL / provider_state / 摘要边界）
* [x] Context 如何生成（`agent-loop.md` 1.6，`TranscriptInput` 作为原料）
* [x] AgentRunner 如何调用（`agent-loop.md` 1.7，`AgentRunSpec` 字段对照表）
* [x] Response 如何返回（`agent-loop.md` 1.8，`OutboundMessage` 与流式收尾）

### 1.3 理解 AgentRunner

重点分析：

* [x] LLM 调用（`agent-loop.md` 2.3，请求前先过 ContextGovernor）
* [x] Tool Calling（`agent-loop.md` 2.4，assistant(tool_calls) → tool 消息）
* [x] 多轮 Tool Loop（`agent-loop.md` 2.2，一次迭代的两条分支）
* [x] 最大循环次数（`agent-loop.md` 2.5，`max_iterations` 默认 200 + `for/else` 兜底总结）
* [x] Tool Error Handling（`agent-loop.md` 2.4，四类失败都转成可读提示 + 两个节流护栏）
* [x] Agent Termination（`agent-loop.md` 2.5，`stop_reason` 全部取值与来源）

### 1.4 理解 Tool System

分析：

```text
BaseTool
   ↓
Tool Registry
   ↓
Tool Discovery
   ↓
Tool Execution
```

完成：

* [x] BaseTool（`tool-system.md` 1，抽象契约 + `read_only`/`concurrency_safe`/`exclusive`）
* [x] Tool Schema（`tool-system.md` 1.3，手写 JSON Schema + `@tool_parameters` + 自研校验）
* [x] Registry（`tool-system.md` 2，稳定定义顺序、缓存失效、`prepare_call` 校验网关）
* [x] Tool Call（`tool-system.md` 4.1，按并发安全性分批 + 顺序保证）
* [x] Tool Result（`tool-system.md` 1.2 与 4.2，`ToolResult(str)` 的 `is_error` 与四类失败语义）

### 1.5 理解 Context

结果：`docs/context.md`。计划书列的 5 个组成部分之外，额外搞清了 3 件事：
「原始转录 ≠ 模型请求」、`input_budget = context_window - max_output - 1024`、
以及 `fit_to_budget` 的四步结构修复（裁剪 → 删孤儿 tool 结果 → 补缺失 tool 结果 → 校验能否装下）。

分析 Context 中包含：

```text
System Prompt
+
Conversation
+
Memory
+
Tools
+
Other Context
```

### 1.6 理解 Memory

重点分析：

* [x] Session History（`memory.md` 2.3，`get_history` 从 `last_archived` 起重放）
* [x] Long-term Memory（`memory.md` 1.1，SOUL / USER / MEMORY.md / history.jsonl 四个文件）
* [x] Memory Loading（`memory.md` 4，`build_system_prompt` 读取 `MEMORY.md`）
* [x] Memory Saving（`memory.md` 2，归档 → 摘要 → history.jsonl；Dream 改写长期记忆）
* [x] Memory 与 Context 的关系（`memory.md` 4，两条进入路径与三个推论）

## 阶段产出

创建：

```text
docs/
├── architecture.md
├── agent-loop.md
├── tool-system.md
├── context.md
└── memory.md
```

结果（2026-09-20，均已落地）：

| 文档 | 行数 | 内容 |
| --- | ---: | --- |
| `docs/architecture.md` | 178 | 启动路径、消息流、四条关键边界、模块地图、深入阅读索引 |
| `docs/agent-loop.md` | 372 | AgentLoop 装配与 7 阶段流水线；AgentRunner 主循环、上限、注入、checkpoint |
| `docs/tool-system.md` | 299 | Tool 契约、Schema、Registry、自动发现、并发执行、错误与安全边界 |
| `docs/context.md` | 303 | system prompt 分层、transcript 组装、预算公式、四步拟合、摘要压缩、空闲压缩 |
| `docs/memory.md` | 293 | Session vs Memory、history.jsonl、归档与摘要检查点、Dream、记忆进入上下文 |

记录：`docs/records/phase-1-source-reading.md`

## 验收标准

能够不看源码解释：

> 一次 Agent 请求从输入到输出经历了什么？

并能够手动画出完整架构图。

结论（完整答案见 `docs/architecture.md` 3.3 与 `docs/agent-loop.md`）：输入 → `InboundMessage`
→ `MessageBus` → `AgentLoop` 七阶段（restore / compact / command / build / run / save / respond）
→ `build` 阶段用 `SessionManager` 历史 + `Memory` 组出 `TranscriptInput` → `AgentRunner` 在
`max_iterations` 内循环「请求模型（请求前经 `ContextGovernor` 拟合预算、必要时摘要压缩）→ 执行工具（按
并发安全性分批）→ 回灌 tool 结果」→ `stop_reason` 判定终止 → 写回会话与摘要检查点 → `OutboundMessage`
与流式事件回频道。

---

# Phase 2：核心代码迁移

## 目标

不要继续直接修改 nanobot，而是开始建立自己的 Framework。

核心原则：

> 迁移核心思想和必要实现，同时重新设计自己的模块边界。

前置：Phase 0 骨架（`src/myagent/`、`myagent.config`、`scripts/check.sh`）与 Phase 1 文档
（迁移取舍表见 `docs/agent-loop.md` 第 3 节、`docs/tool-system.md` 第 5 节、
`docs/context.md` 第 4 节、`docs/memory.md` 第 5 节）。

产物定位：**Framework V1——能独立跑通，但不追求模块解耦**（解耦是 Phase 3 的任务）。

## 2.1 建立 Framework 目录

```text
src/myagent/
├── agent/
│   ├── loop.py            # AgentLoop：build → run → save → respond
│   ├── runner.py          # AgentRunner：模型-工具循环
│   ├── context.py         # ContextBuilder（Phase 6 重写为 ContextManager）
│   └── types.py           # Message / Usage / StopReason / 事件类型
├── models/
│   ├── base.py            # BaseModel / LLMResponse / LLMError
│   └── openai_compat.py   # OpenAI 兼容实现（DashScope 走同一路径）
├── tools/
│   ├── base.py
│   ├── registry.py
│   └── builtin/
├── memory/
│   ├── base.py            # V1：文件式记忆接口，Phase 4 换成三层实现
│   └── store.py
├── session/
│   └── manager.py
├── config/                # 已存在：env.py / settings.py，Phase 2 追加 LLMSettings
└── cli.py                 # console script: myagent chat
```

依赖与决策：本阶段引入 `openai>=1.50`（OpenAI 兼容客户端）；CLI 用标准库 `argparse`
（不引入 typer/rich，保持运行时依赖最小）；两条都记入 ADR-0006「Phase 2 新增依赖的理由」。

结果（2026-09-20）：目录按上表落地，另有 4 个计划外文件——`agent/__init__.py`、
`memory/__init__.py`、`tools/builtin/paths.py`（工作区路径校验，被 `read_file` / `search_local`
复用）、`src/myagent/__main__.py`（支持 `python -m myagent`）。

## 2.2 Model 抽象（models/）

设计：

```python
@dataclass(frozen=True, slots=True)
class ToolCallRequest:
    id: str
    name: str
    arguments: dict[str, Any]

@dataclass(frozen=True, slots=True)
class LLMResponse:
    content: str | None
    tool_calls: list[ToolCallRequest]
    finish_reason: str          # stop / tool_calls / length / error
    usage: Usage | None

class BaseModel(Protocol):
    async def generate(
        self, messages: list[Message], *, tools: list[dict[str, Any]] | None = None
    ) -> LLMResponse: ...
    async def stream(...) -> AsyncIterator[str]: ...      # 接口预留，V1 可只实现非流式
    def count_tokens(self, messages, tools) -> int | None: ...   # Phase 6 的预算用
```

实现至少一个：

```text
OpenAICompatModel   → base_url 指向 DashScope 兼容端点（LLM_BASE_URL）
```

要求：

* [x] LLM 抽象（`BaseModel` + `LLMError` / `ContextWindowExceeded` 异常语义）
      → `src/myagent/models/base.py:31`、`:71`
* [x] Message Format（user / assistant(tool_calls) / tool 三种角色，含 tool_call_id）
      → `src/myagent/agent/types.py:100`（`Message`，带 `to_dict` / `from_dict`）
* [x] Tool Calling（`Tool.to_schema()` → OpenAI function，参数解析失败要能被上层看见）
      → `src/myagent/tools/base.py:220`、`src/myagent/agent/types.py:30`（`parse_error` 承载解析失败）
* [x] Settings（`LLMSettings`：`LLM_PROVIDER` / `LLM_MODEL` / `LLM_API_KEY` / `LLM_BASE_URL` /
      `LLM_MAX_TOKENS` / `LLM_CONTEXT_WINDOW` / `AGENT_MAX_ITERATIONS`，沿用 `myagent.config` 的单入口风格）
      → `src/myagent/config/settings.py:376`（`LLMSettings`）、`:339`（`AgentSettings`）
* [x] `tests/test_models.py`：用假客户端验证请求形状、tool_calls 解析、错误映射（不打真实网络）
      → 382 行，覆盖请求形状、tool_calls 解析与 8 类错误映射

## 2.3 Tool 抽象（tools/）

设计：

```python
class ToolResult(str):                 # 与上游同构：str 子类 + is_error
    is_error: bool
    @classmethod
    def error(cls, content: str) -> ToolResult: ...

class Tool(ABC):
    name: str
    description: str
    @property
    def parameters(self) -> dict[str, Any]: ...
    read_only: bool = False            # 决定是否允许并发
    exclusive: bool = False

    @property
    def concurrency_safe(self) -> bool:
        return self.read_only and not self.exclusive

    async def execute(self, **kwargs: Any) -> ToolResult: ...
```

Registry（`tools/registry.py`）必须实现四件事：

```text
register / get / get_definitions（按名字稳定排序，便于 prompt 缓存）
prepare_call(name, params) -> (tool, params, error)   # 名字纠错 + 类型纠正 + schema 校验，不抛异常
execute(name, params)                                  # 兜底执行入口
```

内置工具（`tools/builtin/`，全部显式注册，本阶段不做自动发现）：

```text
calculator     只读，用于验证参数校验与并发
current_time   只读
read_file      只读，限制在工作区目录内
search_local   受控的本地检索桩（Phase 7 替换为真实论文检索）
```

* [x] 4 个工具可被模型正确调用，参数错误时返回可读提示而不是抛异常
      → `src/myagent/tools/builtin/__init__.py:30`（显式注册 4 个工具）
* [x] `tests/test_tools.py`：类型纠正（`"3"` → `3`）、schema 校验错误文案、名字纠错、并发分批、错误语义
      → 515 行；校验在 `src/myagent/tools/base.py:238`，纠错在 `src/myagent/tools/registry.py:64`

## 2.4 Agent Runner（agent/runner.py）

实现：

```text
LLM
 ↓
Response
 ↓
Tool Call?
 ├── No → Final
 │
 └── Yes
      ↓
   Execute Tool
      ↓
   Tool Result
      ↓
      LLM
```

契约：

```python
@dataclass(slots=True)
class AgentRunSpec:
    messages: list[Message]
    tools: ToolRegistry
    model: BaseModel
    max_iterations: int
    max_tool_result_chars: int
    hooks: list[AgentHook] | None = None
    injection_callback: Callable[[], Awaitable[list[Message]]] | None = None

@dataclass(slots=True)
class AgentRunResult:
    final_content: str | None
    messages: list[Message]
    tools_used: list[str]
    stop_reason: str            # completed / max_iterations / error / empty_final_response
    usage: Usage | None
```

增加：

* [x] max_iterations（`for iteration in range(...)` + 到上限后的统一收尾）
      → `src/myagent/agent/runner.py:90`、`:130`
* [x] Tool timeout（单工具超时 + 超时转成可读工具错误）→ `src/myagent/agent/runner.py:181`
* [x] Tool error handling（工具异常/ToolResult.error → 提示文本回灌，不中断本轮）
      → `src/myagent/tools/registry.py:153`（统一追加 retry hint）
* [x] Tool result 截断（`max_tool_result_chars`）→ `src/myagent/agent/runner.py:213`
* [x] termination condition（`stop_reason` 四个取值 + 空回复重试上限 2）
      → `src/myagent/agent/types.py:90`、`src/myagent/agent/runner.py:31`
* [x] 中途注入（`injection_callback`，接口保留；Loop 侧 V1 可以先不启用）
      → `src/myagent/agent/runner.py:188`（`_drain_injections`）

**本阶段明确不做**（Phase 3/9 再评估）：长度续写（length recovery）、畸形 tool_calls 重试、
provider 原生状态、三阶段 checkpoint 恢复。

## 2.5 Agent Loop（agent/loop.py）

完成：

```text
Message
 ↓
Session
 ↓
Context
 ↓
AgentRunner
 ↓
Response
```

阶段（比上游的 7 阶段少两个，`restore` 与 `compact` 合并进 `build`）：

```text
build    取 session 历史 → ContextBuilder 组装 messages
run      AgentRunner.run(spec) → AgentRunResult
save     追加新消息到 Session（JSONL，与上游一致的追加式存储）
respond  生成 OutboundMessage / 直接返回文本
```

* [x] 会话内串行：`asyncio.Lock` per session_key（跨会话天然并发）
      → `src/myagent/agent/loop.py:281`（`_session_lock`）
* [x] `run_once(user_input, session_key) -> str` 供 CLI 与测试直接调用
      → `src/myagent/agent/loop.py:143`（`run_once`）、`:152`（`_process` 串行入口）
* [x] `run()` 消费 `MessageBus`（Phase 2 保留最小 Bus 实现）
      → `src/myagent/agent/loop.py:54`
* [x] Session 存储：JSONL + `last_archived` 字段预留（Phase 4 用），不做 workspace 命名空间迁移
      → `src/myagent/session/manager.py:58`、`:115`（落盘为 `data/sessions/cli%3Adefault.jsonl`）

## 2.6 CLI

```bash
myagent chat -m "现在几点？"     # 单条
myagent chat                     # 交互（/exit、/session、/clear）
myagent tools                    # 列出已注册工具（调试用）
```

## 阶段产出

能够执行：

```bash
myagent chat
```

实现：

```text
User
 ↓
My Agent Framework
 ↓
LLM
 ↓
Tool
 ↓
Response
```

另外产出 `docs/design.md`（模块职责、接口签名、与上游的差异表）与
`docs/decision-records/0006-phase2-dependencies.md`（新增依赖的理由）。

结果（2026-09-20，均已落地）：

| 产出 | 规模 | 内容 |
| --- | ---: | --- |
| `src/myagent/`（Framework 本体） | 30 个源文件 3112 行 | `agent/`（types · runner · loop · context）、`models/`、`tools/` + 4 个内置工具、`session/`、`memory/`（未接线）、`cli.py` + `__main__.py` |
| `docs/design.md` | 407 行 | 模块地图、一次请求的代码路径、模块契约、与上游的差异表（保留 / 简化 / 加法）、本阶段不做的事（Phase 3 已重写为 800 行的 V2 设计，见下） |
| `docs/decision-records/0006-phase2-dependencies.md` | 57 行 | `openai>=1.50` 与标准库 `argparse` 的取舍、备选方案与后果 |
| `docs/records/phase-2-migration.md` | 262 行 | 工作记录：Baseline → 方案 → 实现 → 真实 transcript → 质量门 → 过程中修掉的两个真实 bug |
| `tests/` | 15 个文件 2952 行 | 258 项测试（Phase 1 基线 63 项），覆盖率 100%（1499 stmts / 398 branches） |

命令行入口由 `pyproject.toml` 的 `[project.scripts]` 暴露：
`myagent chat` / `myagent chat -m "..."` / `myagent tools`，等价写法 `python -m myagent`
（`src/myagent/__main__.py:1`）。

## 验收标准

完全脱离 nanobot 仓库后：

```text
kyobot（src/myagent）
```

仍然可以独立运行。

具体判定：

* [x] 把 `nanobot/` 移出仓库（或删除）后，`myagent chat -m "..."` 仍能完成一次对话 + 一次工具调用
* [x] `scripts/check.sh` 全绿（ruff / mypy strict / pytest），新增模块覆盖率不低于 Phase 0 基线
* [x] 真实 transcript（含一次 calculator 或 read_file 调用）写进 `docs/records/phase-2-migration.md`

结论（证据见 `docs/records/phase-2-migration.md` §7）：

1. **独立可运行**：验收时 `nanobot/` 被改名为 `nanobot.moved`，`myagent chat -m "..."` 正常回答；
   模型在**一轮**里并行调用了 `current_time` / `calculator` / `read_file` 三个工具
   （落盘第 3～6 行），同一会话的第二条消息复用历史把 60 再乘 2 得到 120。
2. **质量门**：`scripts/check.sh` 全绿——46 个文件已格式化、mypy strict 覆盖 30 个源文件 0 问题、
   258 项测试（1499 stmts / 398 branches，100% 覆盖），覆盖率不低于 Phase 1 基线（63 项测试、248 stmts）。
3. **取舍可讲**：`docs/design.md` §4 把每一处差异归入「保留 / 简化 / 加法」，§5 列出本阶段不做的事
   作为 Phase 3 的输入（Phase 3 后 §5 更名为「尚未实现（Phase 4+ 的输入）」）；过程中发现并修掉的两个真实问题（会话重复落盘、测试泄漏真实 `.env`）
   连同修复与回归测试记在记录 §8。

记录：`docs/records/phase-2-migration.md`

---

# Phase 3：Agent Framework 重构

## 目标

从「迁移过来的代码」升级为「自己的 Framework 设计」：把 Phase 2 里耦合在一起的
Loop / Context / Tool / Model / Memory 拆成可替换的模块，再用统一的扩展点串起来。

三个变化：

1. **依赖倒置**：`AgentLoop` / `AgentRunner` 只依赖接口，不依赖具体实现（OpenAI 客户端、Qdrant、SQLite）。
2. **职责收敛**：Loop 只做「取消息 → 执行 → 回写」；上下文怎么拼、记忆怎么检索、文档怎么召回都不在 Loop 里。
3. **扩展点显式**：新增模型、工具、记忆存储、检索器，只需实现一个 Protocol 并在装配处注册。

前置：Phase 2 的 Framework V1（Loop/Runner/Tools/CLI 可跑通）＋ Phase 1 的迁移取舍表
（`docs/agent-loop.md` 第 3 节、`docs/tool-system.md` 第 5 节、`docs/context.md` 第 4 节、`docs/memory.md` 第 5 节）。

产物定位：**Framework V2——模块可替换、依赖可注入**，为 Phase 4/5/6 提供接入点。

## 3.1 模块职责与「不做什么」

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

## 3.2 解耦 AgentLoop

拆两件事：**Loop 不 new 任何东西**，**阶段可注入**。

```python
class AgentLoop:
    def __init__(
        self,
        *,
        model: BaseModel,              # 接口，而不是 OpenAICompatModel
        tools: ToolRegistry,
        context: ContextManager,
        sessions: SessionStore,
        runtime: AgentRuntimeConfig,
    ) -> None: ...
```

阶段契约（每一步都是独立可测的一步，便于单测与计时）：

```text
build    (session, user_input) -> ContextRequest      # 交给 ContextManager
run      (ContextRequest)      -> AgentRunResult      # 交给 AgentRunner
save     (session, result)     -> None                # 交给 SessionStore
respond  (result)              -> OutboundMessage
```

* [x] `AgentLoop` 构造函数里不出现 `AsyncOpenAI` / `QdrantClient` / `sqlite3` 等具体类型
      → `src/myagent/agent/loop.py:122`（只收 `BaseModel` / `ToolRegistry` / `ContextManager` /
      `SessionStore` / `AgentRuntimeConfig`）；`tests/test_contracts.py:442` 用 AST 检查强行守住
* [x] `myagent/agent/runtime.py`：`AgentRuntimeConfig`（`max_iterations` / `tool_timeout_s` /
      `max_tool_result_chars` / `context_budget_tokens`）集中管理运行参数——对齐上游把
      `max_tool_iterations`、`max_tool_result_chars` 放在配置层（`config/schema.py:129`）
      → `src/myagent/agent/runtime.py:33`；预算公式在 `:73`（`context_window - max_tokens - 1024`）
* [x] 单测：注入假 model + 假 tools + 假 context，验证 4 个阶段的调用顺序与失败传播
      → `tests/test_contracts.py:341`（四个假件跑通整条链路）、`tests/test_loop.py:245`
      （超预算在 build 阶段失败且不调用模型）、`tests/test_loop.py:258`（build 把 ContextRequest
      原样交给 ContextManager）

## 3.3 ContextManager：从「拼字符串」到「可裁剪的 section」

```python
@dataclass(frozen=True, slots=True)
class ContextSection:
    name: str                     # system / conversation / memory / rag / tools
    priority: int                 # 越小越先保留
    required: bool                # 不可裁剪
    content: str | list[Message]
    budget_tokens: int | None

class ContextManager(Protocol):
    def build(self, request: ContextRequest) -> list[Message]: ...
    def compact(self, session: Session) -> CompactionReport: ...
```

* [x] V1（Phase 3）只做「section 拼装 + token 估算 + 超限报错」；真正的优先级裁剪与压缩在 Phase 6
      → `src/myagent/agent/context.py:229`（section 组装）、`:246`（超限抛 `ContextBudgetExceeded`）；
      优先级常量照 PLAN 6.1 落成 `:55`～`:59`
* [x] 保留上游的关键区分：**原始转录 ≠ 模型请求**（`docs/context.md` 第 0 节）。因此 `build()` 的输入是
  `history + memories + rag_chunks + tools`，输出才是待发送的 messages
      → 输入是 `ContextRequest`（`src/myagent/agent/context.py:131`），输出是 `ContextBundle.messages`（`:124`）
* [x] 预留 `CompactionReport`（本轮是否压缩、压缩掉多少 token），供 Phase 6/8 使用
      → `src/myagent/agent/context.py:165`；`compact()` 在 `:256` 返回空报告（Phase 6 填实现）

## 3.4 统一接口（Protocol 而非 ABC）

六个扩展点，全部用 `typing.Protocol`（结构类型），实现方无需继承：

| 接口 | 位置 | 最小方法签名 | 实现者 |
| --- | --- | --- | --- |
| `BaseModel` | `models/base.py` | `generate(messages, tools) -> LLMResponse` | `OpenAICompatModel` |
| `BaseTool` | `tools/base.py` | `execute(**kwargs) -> ToolResult` | 内置工具（上游契约参照 `agent/tools/base.py:159`） |
| `BaseMemory` | `memory/base.py` | `add(record)` / `search(query, kind, top_k)` | Phase 4 的分层实现 |
| `BaseEmbedder` | `rag/embedder.py` | `embed(texts) -> list[list[float]]` | `DashScopeEmbedder`（Phase 5） |
| `BaseVectorStore` | `rag/vectorstore.py` | `upsert(...)` / `search(vector, top_k, filters)` | `QdrantVectorStore`（Phase 5） |
| `BaseRetriever` | `rag/retriever.py` | `retrieve(query, top_k, filters) -> list[RetrievedChunk]` | `VectorRetriever`（Phase 5） |

* [x] Protocol 就近定义在各模块（或汇总到 `myagent/protocols.py`），保持「换一行 import 就能替换实现」
      → 六个契约分别在 `src/myagent/models/base.py:71`、`src/myagent/tools/base.py:152`、
      `src/myagent/memory/base.py:30`、`src/myagent/rag/embedder.py:15`、
      `src/myagent/rag/vectorstore.py:19`、`src/myagent/rag/retriever.py:19`；
      **没有**建 `protocols.py`（理由见 ADR-0007「备选方案」）
* [x] 装配只发生在 `myagent/runtime.py::build_agent(config)` 一处（见 3.5）
      → `src/myagent/runtime.py:38`；`tests/test_contracts.py:446` 断言三个具体实现只在
      `myagent.runtime` 的 import 里出现
* [x] 决策记 **ADR-0007**：扩展点用 `Protocol` + 装配注入，而不是 `isinstance` 分支或继承抽象基类
      → `docs/decision-records/0007-framework-extension-points.md`
* [x] `tests/test_contracts.py`：给每个 Protocol 写一个最小假实现，验证 `AgentRunner` 在不 import
      具体实现的前提下即可工作
      → `tests/test_contracts.py:252`（六个契约都是结构类型）、`:305`（假工具 + 假模型驱动 runner）、
      `:341`（四个假件跑通 loop）；`ContextManager` / `SessionStore` 两个 Loop 契约在 `:261`

## 3.5 装配与配置

```python
# src/myagent/runtime.py
def build_agent(settings: Settings) -> AgentLoop: ...
```

```text
Settings（.env）
   │
   ├── LLMSettings        → OpenAICompatModel
   ├── SQLiteSettings     → SqliteSessionStore /（Phase 4）MemoryStore
   ├── QdrantSettings     →（Phase 5）QdrantVectorStore
   └── EmbeddingSettings  →（Phase 5）DashScopeEmbedder
                              │
                              ▼
                    ContextManager + ToolRegistry + AgentLoop
```

* [x] 复用 Phase 0/2 的配置单入口（`src/myagent/config/settings.py`）；组件**只接收 settings 对象，不读环境变量**
      → `Settings`（`src/myagent/config/settings.py:506`，`from_env()` 在 `:404`）；默认组件的构造
      见 `src/myagent/runtime.py:53`（模型）、`:54`（工具）、`:57`（上下文）、`:60`（会话）、`:63`（运行参数）
* [x] CLI 只调用 `build_agent()`，不手工 new 组件
      → `src/myagent/cli.py:227`（chat）、`:64`（tools）；Phase 2 的 `build_agent_loop()` 已删除

## 阶段产出

```text
src/myagent/
├── runtime.py           # build_agent（唯一装配点）
├── protocols.py         # 可选：跨模块 Protocol 汇总
└── agent/
    ├── loop.py          # 改为依赖注入
    ├── runner.py        # 只依赖 BaseModel / ToolRegistry
    ├── context.py       # ContextManager + ContextSection
    ├── session.py       # SessionStore 接口 + JSONL 实现
    └── runtime.py       # AgentRuntimeConfig
docs/
├── design.md            # 模块职责 / 接口签名 / 装配图 / 与上游差异表
└── decision-records/0007-framework-extension-points.md
tests/
└── test_contracts.py
```

`docs/design.md` 必须包含：模块职责表（3.1）、六个接口签名（3.4）、装配图（3.5），
以及「与上游 nanobot 的差异表」——每条差异都要给出理由与上游锚点。

结果（2026-09-20，均已落地）：

| 产出 | 规模 | 内容 |
| --- | ---: | --- |
| `src/myagent/runtime.py` | 65 行 | 唯一装配点 `build_agent`：6 个 kwarg 各自覆盖一个组件 |
| `src/myagent/agent/runtime.py` | 78 行 | `AgentRuntimeConfig`（迭代上限 / 工具超时 / 截断 / 上下文预算） |
| `src/myagent/agent/context.py` | 295 行 | section 模型 + token 估算 + 超预算报错 + 空 `compact()` |
| `src/myagent/session/` | 2 文件 210 行 | `base.py` 契约（`Session` / `SessionStore`）+ `manager.py` JSONL 实现 |
| `src/myagent/rag/` | 5 文件 173 行 | 数据模型 + `BaseEmbedder` / `BaseVectorStore` / `BaseRetriever` |
| `src/myagent/tokens.py` | 49 行 | 全框架共用的 `estimate_tokens` |
| `src/myagent/agent/loop.py` | 263 行 | 4 阶段改为契约注入；`TurnContext.error`；超预算不发请求 |
| `docs/design.md` | 800 行 | V2 设计：职责表 / 八个契约签名 / 装配图 / 差异表 / 四个答辩问题 |
| `docs/decision-records/0007-framework-extension-points.md` | 87 行 | Protocol + 装配注入的决策、后果与 6 个备选方案 |
| `docs/records/phase-3-refactor.md` | 239 行 | 工作记录：Baseline → 方案 → 实现 → 真实 transcript → 质量门 → 修掉的两个真实问题 |
| `tests/test_contracts.py` + `tests/test_runtime.py` | 2 文件 552 行 | 契约 / 边界 / 装配的测试（含 AST import 检查） |

`src/myagent/` 从 30 个文件 3112 行增长到 **39 个文件 3910 行**。

两处与上面的文件清单不同（都是同一取舍的结果）：

- 计划里的 `agent/session.py` 落成 `session/base.py`（契约）+ `session/manager.py`（JSONL 实现）——
  会话不属于「一轮对话的编排」；
- `protocols.py` 没有创建：契约就近定义在各模块，取舍记在 ADR-0007 的「备选方案」。

## 验收标准

**能力判定**

* [x] 换模型（DashScope → 本地 OpenAI 兼容端点）只改 `.env`，不改业务代码
* [x] 新增一个工具只需 `registry.register(...)`，`AgentRunner` / `AgentLoop` 零改动
* [x] `AgentRunner` 的 import 里不出现 `openai` / `qdrant_client`
* [x] `scripts/check.sh` 全绿，`tests/test_contracts.py` 覆盖六个 Protocol

**答辩判定**（答案写进 `docs/design.md`，每条都要指向上游锚点或本仓库代码）

> 为什么 AgentLoop 不应该直接负责 RAG？

> 为什么 Memory 和 RAG 要拆成独立模块，而不是合并成一个 Retrieval 模块？

> 为什么 Tool 需要 Registry，而不是一个 dict + 分支？

> 为什么用 Protocol 而不是 ABC？

结论（证据见 `docs/records/phase-3-refactor.md` §7）：

1. **可替换是可执行的事实**：`tests/test_contracts.py` 用「六个契约的结构类型断言 + 不继承任何
   东西的假件跑通链路 + AST 检查核心模块的 import」三层把这条边界固定下来——任何一次
   「核心 import 具体实现」都会立刻失败，而不是等评审发现。
2. **装配收敛到一处**：`build_agent()`（`src/myagent/runtime.py:38`）是唯一知道实现类的地方，
   CLI 只调用它；Phase 2 记录里的遗留项「装配仍在 `cli.build_agent_loop()`」到此清账。
3. **重构没有改变运行时语义**：真实 transcript（`docs/records/phase-3-refactor.md` §7.2）里
   「一次模型请求 → 3 个只读工具并发 → 一次回答」、第二轮复用历史得到 120，与 Phase 2 一致。
4. **质量门**：`scripts/check.sh` 全绿——58 个文件已格式化、mypy strict 覆盖 39 个源文件 0 问题、
   304 项测试（1761 stmts / 434 branches，100% 覆盖），文档锚点 691 个全部解析。
5. **答辩问题**：四个问题（为什么 Loop 不负责 RAG / 为什么 Memory 与 RAG 分开 /
   为什么需要 Registry / 为什么用 Protocol）连上游锚点一起写进 `docs/design.md` §6。

记录：`docs/records/phase-3-refactor.md`

---

# Phase 4：Memory 系统改造

## 目标

把 Phase 2 的「单文件长期记忆」升级为**分层 Memory Architecture**，并落进 ADR-0003 选定的
SQLite + Qdrant。这是整个项目最核心的改造之一：上游只有一层长期记忆（`MEMORY.md`，
`agent/memory.py:229` 读写、`agent/memory.py:253` 注入上下文），我们要回答的是
「什么该记住、记多久、怎么召回、怎么不污染」。

前置：

* ADR-0003（SQLite 存文档/元数据，Qdrant 存向量，用 id 关联）
* ADR-0005（DashScope embedding，`EMBED_MODEL_TYPE=dashscope`）
* Phase 3 的 `BaseMemory` / `BaseEmbedder` / `BaseVectorStore` 契约与 `build_agent()` 装配点

## 4.0 分层模型

```text
MemoryManager（门面：write / search / build_context / consolidate）
├── WorkingMemory     本轮对话窗口（不落库，直接从 Session 转录构造）
├── EpisodicMemory    「发生过什么」：事件、任务结论、读过的论文
├── SemanticMemory    「我知道什么」：稳定偏好、长期事实、项目知识
└── MemoryRetriever   embedding + 向量检索 + 时间衰减
```

| 层 | 回答什么 | 生命周期 | 写入触发 | 进向量库 | 退出路径 |
| --- | --- | --- | --- | --- | --- |
| Working | 这次对话到哪了 | 单次会话 | 每轮自动 | 否 | 会话结束即丢弃 |
| Episodic | 过去发生过什么 | 天～周 | 归档检查点 + 抽取器 | 是（带时间戳） | 巩固为 Semantic 或过期清理 |
| Semantic | 我（agent）知道什么 | 长期 | 抽取器 / 显式写入 / 巩固 | 是（不衰减） | 人工回滚 |

三条设计约束：

1. **不重复存储对话原文**：Working Memory 直接从 `SessionStore` 的历史构造
   （对齐上游 `session/manager.py:344` 的 `get_history`），避免「一份对话两处存储」导致的不一致。
2. **Session 与 Memory 的边界不变**：Session 是可重放的原文，Memory 是被提炼的结论
   （对齐上游 `agent/memory.py:58` 的 `MemoryStore` 与 session 的分离）。
3. **写入有策略**：不是所有对话都进长期记忆（见 4.6），否则检索会被噪声淹没。

## 4.1 MemoryRecord（统一数据结构）

```python
# src/myagent/memory/types.py
@dataclass(frozen=True, slots=True)
class MemoryRecord:
    id: str                              # uuid4().hex
    kind: Literal["episodic", "semantic"]
    text: str                            # 一条只承载一个事实，便于独立检索与失效
    session_key: str | None              # 来源会话（Working 层不用）
    created_at: datetime
    importance: float                    # 0..1，写入与排序用
    source: str                          # extractor / manual / consolidation
    metadata: dict[str, Any]             # paper_id、tags 等
```

* [x] 过长的抽取结果先拆句再入库，保证「删除/更新某一事实」可行
* [x] `text` 长度上限（默认 500 字符）与单次条数上限在**写入层**强制，而不是靠 prompt 自觉

## 4.2 Working Memory

* 由 `SessionStore` 的最近 N 轮构造；N 由 `context_budget` 决定（Phase 6 接管预算）
* 本层不落库、不 embed，只提供 `recent_turns(limit)` 视图
* [x] `tests/test_memory.py::test_working_memory_uses_session_history`：验证不产生额外存储

## 4.3 Episodic Memory

* 写入时机：一轮对话的 `save` 阶段之后（归档检查点，对齐上游 `agent/memory.py:996` 的 `archive_session`）
* 记录内容：用户意图、Agent 的关键结论、工具产生的持久产物（如 `save_note`）
* 检索：向量检索 + **时间衰减**（半衰期默认 30 天，可配）
* [x] `search(query, kind="episodic", top_k)`，打分 = `cosine * 0.5 ** (age_days / half_life)`

## 4.4 Semantic Memory

* 记录内容：稳定偏好（「用户偏好 Python」）、长期事实（「用户在研究 GraphRAG」）、项目知识
* 检索：向量检索，不衰减；短 query 时用关键词兜底（向量对短句不敏感）
* 淘汰：只允许人工或巩固流程改写，不自动过期（避免「重要偏好被时间衰减吃掉」）

## 4.5 存储层

**SQLite（`src/myagent/memory/sqlite_store.py`）**

```text
memories(
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,              -- episodic | semantic
    text TEXT NOT NULL,
    session_key TEXT,
    created_at TEXT NOT NULL,        -- ISO8601
    importance REAL NOT NULL,
    source TEXT NOT NULL,
    metadata TEXT NOT NULL           -- JSON
)
memory_vectors(                      -- 记录向量落库状态，避免重复 embedding
    memory_id TEXT PRIMARY KEY REFERENCES memories(id) ON DELETE CASCADE,
    collection TEXT NOT NULL,
    model TEXT NOT NULL,
    dim INTEGER NOT NULL,
    embedded_at TEXT NOT NULL
)
索引：memories(kind, created_at)、memories(session_key)
```

**Qdrant（`src/myagent/memory/vector_index.py`）**

* collection：`myagent_memories`（与文档向量 `myagent_documents` 分开）
* payload：`{memory_id, kind, session_key, created_at}` → 检索时按 `kind` 过滤
* 决策：记 **ADR-0008**「分层记忆的写入策略与向量命名空间」
  * 为什么分 collection：记忆与文档的生命周期/清理策略/embedding 来源都不同。混在一个 collection 里，
    「删掉一篇论文」可能误伤同 collection 的记忆向量；分开后 `delete(document_id)` 与
    `delete(memory_id)` 互不影响
  * 代价：两个 collection、配置项 +1（`MYAGENT_QDRANT_MEMORY_COLLECTION`，默认 `myagent_memories`），
    由 `QdrantSettings` 统一提供

* [x] 存储实现只接收 `SQLiteSettings` / `QdrantSettings`（`src/myagent/config/settings.py`），不直接读 env
* [x] SQLite 默认 `data/myagent.db`，与 Phase 5 的 documents/chunks 同库不同表

## 4.6 写入策略（MemoryExtractor）

```text
一轮对话（user + assistant + tool 结果）
        │
        ▼
MemoryExtractor.extract(turn) -> list[MemoryRecord]      # LLM + JSON schema 输出候选
        │
        ▼
过滤：去重（与最近 K 条余弦 > 0.95 丢弃）
      + 重要度阈值（importance >= 0.5）
      + 长度/条数上限（单条 500 字符、单次最多 3 条）
        │
        ▼
MemoryStore.add(records) → embed → Qdrant upsert → 写 memory_vectors
```

* 抽取用「让模型自己写记忆」（对齐上游 Dream 的思路 `agent/memory.py:543`），但**要加校验**：
  只接受符合 schema 的记录，非法输出直接丢弃并记一条 warning
* 规则兜底：命中「我是 / 我偏好 / 我的项目是」等模式的句子，直接进 Semantic（`importance=0.7`）
* 明确**不写入**：闲聊、一次性查询、工具原始输出、含密钥或隐私的内容
* [x] `tests/test_memory.py::test_extractor_filters`：给 5 类句子，断言「写入哪些、丢弃哪些」
* [x] `tests/test_memory.py::test_dedup`：同一事实写入 3 次，SQLite 仍只有 1 条

## 4.7 检索（MemoryRetriever）

```text
query
 ↓ embed（复用 Phase 3 的 BaseEmbedder）
 ↓ Qdrant search(filter=kind, top_k=5)
 ↓ 时间衰减重排（Episodic）+ 关键词兜底（Semantic）
 ↓ MemoryContext（带 kind / 来源 / 时间，便于引用与评估）
```

* 接口对齐 Phase 3 的 `BaseMemory.search`；Phase 6 的 `ContextManager` 只消费 `MemoryContext`
* [x] 返回结果带 `memory_id`，Phase 8 才能计算「记忆命中率」

## 4.8 巩固（Consolidator，对应上游 Dream）

* 触发：显式命令 `myagent memory consolidate`（**先不做定时任务**，避免运维复杂度）
* 输入：SQLite 中 `kind="episodic"` 且未巩固的记录（按 `created_at` 增量）
* 输出：`kind="semantic"` 的新记录 + 把被巩固的 Episodic 标记为已巩固
* 语义对齐上游：**只有成功才前移游标**（`agent/memory.py:619` 的 `dream_run_completed`）
* 上游参照：`agent/memory.py:1072` 的 `Consolidator`、`agent/memory.py:1103` 的 `summarize_transcript`

## 4.9 CLI

```bash
myagent memory list --kind semantic -n 20
myagent memory search "我的研究方向" -k 5
myagent memory add "用户偏好 Python" --kind semantic --importance 0.8
myagent memory consolidate --dry-run
myagent memory forget <memory_id>
```

## 阶段产出

```text
src/myagent/memory/
├── types.py           # MemoryRecord / MemoryContext
├── base.py            # BaseMemory Protocol（Phase 3 定义，此处补齐实现）
├── manager.py         # MemoryManager 门面
├── working.py         # WorkingMemory
├── episodic.py        # EpisodicMemory
├── semantic.py        # SemanticMemory
├── extractor.py       # MemoryExtractor + 过滤规则
├── retriever.py       # MemoryRetriever（向量 + 衰减 + 关键词兜底）
├── consolidator.py    # Consolidator（Episodic → Semantic）
├── sqlite_store.py    # SQLite 表与读写
└── vector_index.py    # Qdrant collection 与过滤
docs/
├── memory-design.md   # 架构 / 生命周期 / 检索 / 存储 / 注入
└── decision-records/0008-layered-memory.md
tests/test_memory.py
```

`docs/memory-design.md` 必须包含：分层表（4.0）、`MemoryRecord` 字段含义、SQLite 表结构、
Qdrant payload 与过滤、写入策略的「写/不写」清单、与上游 Dream 的对照表。

结果（2026-09-20，均已落地）：

| 产出 | 规模 | 内容 |
| --- | ---: | --- |
| `src/myagent/memory/` | 13 文件 2284 行 | 四层实现 + SQLite / Qdrant 存储 + 检索 + 抽取 + 巩固（`docs/records/phase-4-memory.md` §4） |
| `src/myagent/agent/context.py` | +25 行 | `MemoryProvider` 端口：`recall(query, session_key)` / `observe(session_key, messages)` |
| `src/myagent/agent/loop.py` | +36 行 | build 阶段召回、save 阶段回写；两处失败都只记 warning |
| `src/myagent/runtime.py` | 111 行 | `build_agent(memory=...)` + `build_memory()`（对话与 CLI 共用一套配置） |
| `src/myagent/cli.py` | +148 行 | `memory list/search/add/consolidate/forget` 五个子命令 |
| `docs/memory-design.md` | 310 行 | 分层表 / 字段 / 表结构 / payload / 写与不写清单 / 与上游 Dream 对照 |
| `docs/decision-records/0008-layered-memory.md` | — | collection 分裂、写入策略、衰减、巩固游标、Protocol、装配 |
| `docs/records/phase-4-memory.md` | — | Baseline → 方案 → 实现 → 五张实验表 → 质量门 → 四个真实问题 |
| `tests/test_memory.py` | 1586 行 / 105 项 | 全部离线（假 embedder + 内存向量库），无需 key 与 Qdrant |
| `scripts/memory_experiment.py` | 400 行 | 五张表的实验脚本：真实组件 + Qdrant 嵌入式本地模式 |

`src/myagent/` 从 39 个文件 3910 行增长到 **50 个文件 6386 行**；
测试从 304 项增长到 442 项，覆盖率仍为 100%（2785 stmts / 662 branches）。

三处与上面的文件清单不同（都是同一取舍的结果）：

- 计划里的 `base.py` 只留契约：`MemoryRecord` 落到 `types.py`，
  写路径与存储拆成 `sqlite_store.py` / `vector_index.py` / `manager.py`；
- `FileMemoryStore` 已删除：JSONL + 字面量检索被 SQLite + Qdrant 取代，
  `BaseMemory` 从 4 个方法长到 8 个（多了 `get` / `count` / `forget` / `add_many`）；
- 新增 `memory/embedder.py`：PLAN 4.5 只写了「embed」，但 Phase 4 需要一条现在就能跑的
  embedding 路径；`OpenAICompatEmbedder` 实现 `src/myagent/rag/embedder.py:15` 的 `BaseEmbedder` 契约，
  Phase 5 的文档向量可以直接复用它。

## 验收标准

**功能**（跨 Session 实验）

```text
Session 1  用户：我正在研究 RAG。
Session 2  用户：我最近研究什么方向？
Memory OFF → 无法回答（或答非所问）
Memory ON  → 正确召回「正在研究 RAG」（带来源与时间）
```

* [x] `myagent memory list` 能看到 Session 1 提炼出的 Semantic 记录
* [x] `MYAGENT_MEMORY_ENABLED=false` 后同一问题不再召回（开关口径与 Phase 8 一致）

**实验**（每组都要落进 `docs/records/phase-4-memory.md` 的表格）

* [x] 写入准确率：构造 20 句「偏好/事实/闲聊/一次性查询/工具输出」，人工标注是否该写入，
      报准确率与误写率
* [x] 去重：同一事实重复 3 轮，断言库中只有 1 条
* [x] 巩固：3 条 Episodic → 1 条 Semantic，检查信息是否丢失
* [x] 检索：10 个记忆类问题，报 hit@5 与延迟（Phase 8 复用同一套题）

**工程**

* [x] `scripts/check.sh` 全绿；`tests/test_memory.py` 全部离线（假 embedder + 内存向量库）
* [x] Qdrant 未启动时给出可读错误而不是堆栈，`myagent memory search` 有明确提示

结论（证据见 `docs/records/phase-4-memory.md` §6～§8）：

1. **写入是可判定的策略，不是 prompt 的自觉**：20 句人工标注上规则与 LLM 两条路径准确率都是
   100%、误写率 0%；500 字符 / 3 条 / importance ≥ 0.5 的上限与「不写」清单全部在代码里强制
   （`src/myagent/memory/extractor.py:184`、「写/不写」表见 `docs/memory-design.md` §4.1）；
2. **跨 Session 召回成立**：Session 1 写入、Session 2 召回带层与日期；
   `MYAGENT_MEMORY_ENABLED=false` 时召回为空（`docs/records/phase-4-memory.md` §6.5）；
3. **巩固与上游 Dream 同语义**：只有写成功才前移游标（`src/myagent/memory/consolidator.py:92`），
   3 条 Episodic 稳定折成 1 条 Semantic、逐条信息覆盖率 100%（§6.3）；
4. **降级可读**：Qdrant 连不上时 `MemoryContext.degraded=True` + 一句含 URL 的 note，
   `myagent memory search` 打印提示而不是堆栈（§8.2 对真实 Qdrant 的往返验证）；
5. **实验里暴露并修掉的一个真问题**：Qdrant 把 point id 归一化成带连字符的 UUID，
   原先会让向量命中在回查 SQLite 时全部丢失、静默退回关键词检索；
   现在 payload 里的 `memory_id` 优先（`src/myagent/memory/vector_index.py:214`），§8.2 有往返证据。

记录：`docs/records/phase-4-memory.md`

---

# Phase 5：RAG 系统建设

## 目标

实现一个**端到端、可插拔**的 RAG Pipeline：从 PDF 进来，到「带引用的答案」出去。
上游 nanobot **没有向量检索与 embedding**（包内搜不到相关实现），所以这部分是纯增量能力，
也是技术面试里最容易被追问的部分。

前置：ADR-0003（Qdrant）、ADR-0005（DashScope embedding）、Phase 3 的
`BaseEmbedder` / `BaseVectorStore` / `BaseRetriever` 契约、Phase 4 的 embedding 与向量写入路径。

## 5.0 Pipeline

```text
ingest:  Document → Loader → Chunker → Embedder → VectorStore（幂等，sha256 去重）
query:   Query → Embedder → VectorStore.search → (Reranker) → Retriever → Context
```

## 5.1 数据模型

```python
# src/myagent/rag/types.py
@dataclass(frozen=True, slots=True)
class Document:
    id: str                       # sha256 前 16 位（内容寻址）
    source: str                   # 文件路径或 URL
    title: str | None
    text: str                     # 归一化全文
    metadata: dict[str, Any]      # format / pages / sha256 / added_at

@dataclass(frozen=True, slots=True)
class Chunk:
    id: str                       # f"{document_id}:{index}"
    document_id: str
    index: int
    text: str
    metadata: dict[str, Any]      # page / char_span / heading / token_estimate

@dataclass(frozen=True, slots=True)
class RetrievedChunk:
    chunk: Chunk
    score: float
    document: Document            # 便于组装引用与标题
```

SQLite（与 Phase 4 同库 `data/myagent.db`，表不同）：

```text
documents(id TEXT PK, source TEXT, title TEXT, sha256 TEXT UNIQUE, created_at TEXT, metadata TEXT)
chunks(id TEXT PK, document_id TEXT REFERENCES documents(id) ON DELETE CASCADE,
       idx INTEGER, text TEXT, page INTEGER, metadata TEXT)
索引：chunks(document_id)
```

* `sha256 UNIQUE` 是幂等 ingest 的关键：同一文件重复上传只更新 `source`，不重复建 chunk
* [ ] `tests/rag/test_store.py`：重复 ingest 后 documents/chunks 行数不变

## 5.2 Loader

```python
class BaseLoader(Protocol):
    def supports(self, path: Path) -> bool: ...
    def load(self, path: Path) -> Document: ...
```

| 实现 | 格式 | 依赖 |
| --- | --- | --- |
| `TextLoader` | `.txt` | 标准库 |
| `MarkdownLoader` | `.md` | 标准库（保留标题层级进 `metadata.heading`） |
| `PdfLoader` | `.pdf` | `pypdf`（本阶段新增依赖，记 ADR-0009） |

* 明确不做：OCR、扫描件、表格结构化、公式抽取（列为 non-goal，避免范围膨胀）
* [ ] `tests/rag/fixtures/mini.pdf` 放一个 2 页小 PDF；测试只读本地 fixture，不打网络

## 5.3 Chunker

```python
class BaseChunker(Protocol):
    def split(self, document: Document) -> list[Chunk]: ...
```

* V1：`FixedSizeChunker(size=800, overlap=120)`（按字符），先按段落切，超长再按句号回退，
  让 chunk 尽量落在语义边界上
* 预留 `RecursiveChunker`（`\n\n` → `\n` → `。` → 字符）与中文标点处理
* chunk 的 `metadata.token_estimate` 用统一估算函数，Phase 6 的预算直接复用
* [ ] 参数实验（Phase 8 复盘）：size = 400 / 800 / 1200 的 hit@5 与平均 chunk 长度

## 5.4 Embedder

```python
class BaseEmbedder(Protocol):
    async def embed(self, texts: list[str]) -> list[list[float]]: ...
    @property
    def dim(self) -> int: ...
```

* 默认 `DashScopeEmbedder`：`EMBED_MODEL_TYPE=dashscope`、
  `EMBED_MODEL_NAME=qwen3.7-text-embedding-flash`，走 OpenAI 兼容 `/embeddings`（ADR-0005 已定）
* 备选 `OpenAIEmbedder`：`EMBED_MODEL_TYPE=openai`，作为对比基线
* 维度：`EMBED_DIM` 留空时，首次 ingest 用一次真实调用探测 `len(vector)`，把结果记进
  `.env`（`EMBED_DIM`）与 phase 记录；**Qdrant collection 的 dim 必须等于探测值**
* 批量与重试：单次最多 `EMBED_BATCH_SIZE`（默认 16）条，失败按指数退避重试 3 次
* [ ] 归一化默认开启（cosine 等价于点积）；维度不一致时抛可读错误

## 5.5 VectorStore

```python
class BaseVectorStore(Protocol):
    def ensure_collection(self, dim: int) -> None: ...
    def upsert(self, chunks: list[Chunk], vectors: list[list[float]]) -> None: ...
    def search(self, vector: list[float], top_k: int, filters: Filter | None) -> list[ScoredPoint]: ...
    def delete_document(self, document_id: str) -> None: ...
```

* 实现 `QdrantVectorStore`，collection 默认 `myagent_documents`（`QdrantSettings.collection`）
* payload：`{chunk_id, document_id, page, idx}` → 支持按 `document_id` 过滤（多论文比较要用，见 Phase 7）
* 距离用 Cosine；`ensure_collection` 幂等（已存在且维度一致则跳过）
* 决策记 **ADR-0009**（chunk 参数 + 检索默认参数 + 是否引入 reranker 依赖），依据 Phase 8 的实验数据

## 5.6 Retriever

```python
class BaseRetriever(Protocol):
    async def retrieve(
        self, query: str, top_k: int = 5, *, document_ids: list[str] | None = None
    ) -> list[RetrievedChunk]: ...
```

* `VectorRetriever`：`embed(query)` → `vectorstore.search(filters=document_ids)` → 组装 `RetrievedChunk`
* 返回 `score` 与 `chunk.id`，这是 Phase 8 计算 hit@k 的前提
* `HybridRetriever`（BM25 + 向量 + 融合）留到 §7.1 的可选方向

## 5.7 Reranker（可选）

```python
class BaseReranker(Protocol):
    async def rerank(
        self, query: str, candidates: list[RetrievedChunk], top_n: int
    ) -> list[RetrievedChunk]: ...
```

* V1 提供 `IdentityReranker`（直接截断）与 `ScoreReranker`（按现有 score 排序）
* 模型型 rerank（cross-encoder 或 DashScope rerank API）是否引入，取决于 Phase 8：
  **hit@3 的提升 > 延迟增量** 才引入（结论写进 ADR-0009）

## 5.8 Pipeline 与 CLI

```python
# src/myagent/rag/pipeline.py
class RagPipeline:
    def ingest(self, paths: list[Path]) -> IngestReport: ...                 # 幂等
    async def retrieve(
        self, query: str, top_k: int = 5, document_ids: list[str] | None = None
    ) -> list[RetrievedChunk]: ...
    def build_context(self, chunks: list[RetrievedChunk]) -> str: ...        # 带 [document_id#index] 引用
```

```bash
myagent ingest data/papers/*.pdf
myagent search "GraphRAG 的核心思想" -k 5
myagent docs list
myagent docs delete <document_id>
```

## 阶段产出

```text
src/myagent/rag/
├── types.py         # Document / Chunk / RetrievedChunk
├── loader.py        # BaseLoader + Pdf / Text / Markdown
├── chunker.py       # FixedSizeChunker + 预留 Recursive
├── embedder.py      # DashScopeEmbedder / OpenAIEmbedder + 维度探测
├── vectorstore.py   # QdrantVectorStore
├── retriever.py     # VectorRetriever
├── reranker.py      # Identity / Score
├── store.py         # SQLite documents / chunks
└── pipeline.py      # RagPipeline（ingest / retrieve / build_context）
docs/rag-design.md
docs/decision-records/0009-chunking-and-retrieval.md
tests/rag/{test_loader,test_chunker,test_store,test_pipeline}.py
tests/rag/fixtures/mini.pdf
```

## 验收标准

```text
上传 PDF → 建立知识库 → 提出问题 → 检索相关内容 → LLM 回答
```

* [ ] `myagent ingest data/papers/x.pdf` 后 `myagent search "..."` 返回带 `document_id#index` 的来源
* [ ] 端到端：检索结果经 `build_context()` 注入，回答能引用到具体 chunk（引用可信、可回跳原文）
* [ ] 幂等：同一文件 ingest 两次，`documents` / `chunks` 行数与 Qdrant point 数不变
* [ ] 维度探测：`EMBED_DIM` 留空 → 首次 ingest 自动探测 → collection 建立成功，且 `myagent config check` 报出维度
* [ ] 离线测试：`pytest -m "not smoke"` 不发起任何网络请求（假 embedder + 内存向量库）
* [ ] 实验表（进 `docs/records/phase-5-rag.md`）：chunk size 400/800/1200 的 hit@5、平均 chunk 长度、
      检索延迟，以及「RAG OFF vs RAG ON」的定性对比

---

# Phase 6：Context Manager 重构

## 目标

把 Phase 4 的 Memory 与 Phase 5 的 RAG **正式接进 Agent Context**，并把「优先级 + 预算 +
结构修复 + 压缩」做成可配置、可测试、可观测的一层。

上游把这件事拆在两个文件里：`ContextBuilder`（`agent/context.py:89`，负责分层拼装）与
`ContextGovernor`（`agent/context_governance.py:336`，负责拟合预算）；我们合并为 `ContextManager`，
但保留它们各自的关键语义。

前置：Phase 3 的 `ContextManager` / `ContextSection` 契约、Phase 4 的 `MemoryContext`、
Phase 5 的 `RetrievedChunk`。

## 6.0 最终 Context 结构

```text
System Instruction       指令 + 人格（SOUL 类比）
 + Pinned                本轮必须遵守的约束（不可裁）
 + Recent Conversation   最近 N 轮原文
 + Archived Summary      旧对话的摘要检查点
 + Relevant Memory       分层记忆（带 kind / 时间 / 来源）
 + RAG Context           文档 chunk（带引用）
 + Tools                 工具 schema
          ↓
   ContextManager
          ↓
        LLM
```

每一段都是 Phase 3 定义的 `ContextSection`（`name / priority / required / content / budget_tokens`），
这样「加了什么、裁了什么」在日志里可见，而不是藏在字符串拼接里。

## 6.1 优先级

```text
优先级 0  System + Pinned        required（永不裁剪）
优先级 1  Current Query          required
优先级 2  Recent Conversation
优先级 3  Archived Summary
优先级 4  Relevant Memory
优先级 5  RAG Context
优先级 6  Tools
```

* 数字越小越先保留；当预算不够时，从优先级最大的 section 开始降级
* [ ] `tests/test_context.py::test_priority_order`：断言四来源同时超配时，裁剪顺序符合上表

## 6.2 预算

```text
input_budget = context_window_tokens - max_output_tokens - 1024（安全余量）
```

公式对齐上游实现（`agent/context_governance.py:693`）。各来源的**初始配额**如下，
在 Phase 8 用实验调整（记 ADR-0010）：

| 来源 | 初始比例 | 超配额时的降级动作 |
| --- | ---: | --- |
| Recent Conversation | 35% | 触发 `compact()`（见 6.4） |
| RAG Context | 35% | 减少 chunk 数（先砍低分） |
| Relevant Memory | 20% | 减少记忆条数（先砍低重要度） |
| Other（Pinned/Summary） | 10% | 截断摘要 |

* [ ] `ContextBudget` 从 `AgentRuntimeConfig` 读取，不写死在 `build()` 里
* [ ] 每次 build 产出 `ContextReport`：每段的 `budget / used / dropped`，进日志（Phase 9 结构化）
* [ ] `tests/test_context.py::test_budget_clipping`：四来源都塞满 → 结果 token ≤ budget 且结构合法

## 6.3 结构修复（顺序不能反）

对齐上游 `fit_to_budget` 的四步（`agent/context_governance.py:336`、`agent/context_governance.py:951`）：

```text
1. 按 section 预算裁剪内容
2. 删除孤儿 tool 结果（没有对应 assistant(tool_calls) 的 tool 消息）
3. 补上缺失的 tool 结果（有 tool_call 但没有结果 → 补占位错误消息）
4. 校验总长装得下：装不下就抛 ContextWindowExceeded（不静默截断）
```

原则：**结构合法性优先于省 token**。宁可明确报错，也不要发出一个 provider 会拒绝的请求。

* [ ] `ContextWindowExceeded` 与 Phase 2 的 `LLMError` 语义对齐，CLI 给出可读提示
* [ ] `tests/test_context.py::test_orphan_tool_repair`：构造孤儿/缺失两类畸形历史，断言修复后成对

## 6.4 压缩

* **显式压缩**：`myagent session compact <key>` → 把 `[0, boundary)` 的对话换成一个摘要 checkpoint，
  原文仍留在 JSONL（对齐上游：`session/manager.py:323` 写检查点、`session/manager.py:344` 从
  `last_archived` 重放、摘要由 `agent/memory.py:1103` 生成）
* **自动触发**：`Recent Conversation` 超配额时先裁剪最旧的轮次，仍超则触发压缩
* **空闲压缩**（对齐 `agent/autocompact.py:68`）：默认**关闭**，需要时用 CLI 或定时显式开启
* [ ] 压缩报告写入 `CompactionReport`（压缩前后 token、被摘要的轮数），Phase 8 用它做「压缩 ON/OFF」实验

## 6.5 集成

```python
# Phase 3 的 build_agent() 内部
context = ContextManager(
    budget=runtime.context_budget,
    memory=memory_manager,      # Phase 4
    retriever=rag_pipeline,     # Phase 5
    token_counter=model.count_tokens,
)
```

* 开关：`MYAGENT_MEMORY_ENABLED` / `MYAGENT_RAG_ENABLED`；关闭时对应 section 直接为空
  （这就是 Phase 8 做 ON/OFF 实验的开关）
* [ ] `ContextManager` 不 import `QdrantClient` / `sqlite3`：它只调用 `BaseMemory.search` 与
      `BaseRetriever.retrieve`

## 阶段产出

```text
src/myagent/agent/
├── context.py        # ContextManager / ContextSection / ContextBudget / ContextReport
├── token_budget.py   # 统一 token 估算（Phase 5 的 chunk 估算复用同一函数）
└── compaction.py     # compact() + 摘要策略
docs/
├── context-design.md # 在 Phase 1 的 docs/context.md 之上补「我们的设计」
└── decision-records/0010-context-budget.md
tests/test_context.py
```

## 验收标准

* [ ] 一次请求的 transcript 里同时出现 `Conversation / Memory / RAG / Tools` 四段
      （用 `myagent chat --show-context` 打印验证）
* [ ] 预算实验：四来源塞满 → 裁剪后 token ≤ `input_budget`，且 `ContextReport` 记录了每段的降级动作
* [ ] 压缩实验：长会话（≥ 20 轮）压缩后请求 token 下降 ≥ 40%，且 3 个探针问题仍能答对（关键信息未丢）
* [ ] 开关实验：关掉 RAG → RAG section 为空；关掉 Memory → Memory section 为空
* [ ] 单测覆盖：优先级、配额分配、孤儿修复、超限报错、压缩报告
* [ ] `scripts/check.sh` 全绿

**答辩**（答案写进 `docs/context-design.md`）

> Context 太长怎么办？

> 为什么先删孤儿 tool 结果，而不是先删历史对话？

> 为什么摘要压缩要保留原文？

---

# Phase 7：垂直领域 Agent

## 目标

用自研 Framework 构建一个真实应用（Research Agent），验证可扩展性：
**只加工具与 prompt，不改 Framework 内核**。

前置：Phase 4（Memory）/ Phase 5（RAG）/ Phase 6（Context）可用；新建 `examples/research_agent/`。

## 7.0 场景与边界

面向论文阅读、技术资料研究、知识库问答。

| 场景 | 输入 | 依赖能力 |
| --- | --- | --- |
| 单论文问答 | 一篇 PDF + 问题 | RAG（`document_ids` 过滤） |
| 多论文比较 | 多篇 PDF + 对比问题 | RAG 跨文档 + 引用 |
| 跨 Session 记忆 | 两次会话 | Episodic / Semantic Memory |
| 工具调用 | 综合问题 | Tool Calling（检索 + 笔记 + 记忆） |

明确**不做**：联网抓取论文、PDF 图表理解、自动生成综述（non-goal，避免范围膨胀）。

## 7.1 工具

```text
list_papers    只读   列出已入库文档（document_id / title / 页数）
search_paper   只读   RagPipeline.retrieve（支持 document_ids 过滤）
read_paper     只读   按 document_id + 页范围取原文（只读 ⇒ concurrency_safe）
save_note      写     写入 SemanticMemory（source="tool"，importance 由模型给出）
search_memory  只读   MemoryRetriever.search
```

* 契约沿用 Phase 3 的 `BaseTool`（上游参照 `agent/tools/base.py:159`），注册走 `ToolRegistry`
  （`agent/tools/registry.py:19`）
* `read_paper` 必须限制在 `data/papers/` 内（路径护栏思想对齐上游读文件工具 `agent/tools/filesystem.py:270`）
* [ ] `tests/examples/test_research_tools.py`：5 个工具的 schema、参数校验、错误语义
      （`save_note` 用假 store，不打网络）

## 7.2 Agent 装配

```python
# examples/research_agent/agent.py
def build_research_agent(settings: Settings) -> AgentLoop:
    return build_agent(settings, tools=research_tools, system_prompt=RESEARCH_PROMPT)
```

* 复用 Phase 3 的唯一装配入口 `build_agent()`；应用层只提供 **工具集合 + system prompt + 领域配置**
* 会话用 `--session` 复用；跨 Session 演示靠 Memory，而不是把历史塞进 prompt

## 7.3 Agent Flow

```text
User Query
     │
     ▼
Research Agent（build_agent 装配）
     │
     ├──── Memory Retrieval（search_memory / Context 自动注入）
     ├──── Paper RAG（search_paper / read_paper）
     ├──── Tool Calling（save_note）
     └──── LLM
              │
              ▼
        Answer（带 [document_id#index] 引用）
```

## 7.4 Prompt 与引用规范

`prompts.py` 里的 system prompt 必须写清这四条：

```text
1. 先检索再回答（search_paper / search_memory）；检索不到就明说「知识库中没有」
2. 引用格式 [<document_id>#<index>]，回答里的每个事实都要带引用
3. 长文比较时先分别总结再对比，分别给出引用
4. 用户偏好与关键结论用 save_note 落库，不要把无价值闲聊写进记忆
```

* [ ] 引用可回跳：拿到 `[doc#idx]` 能在 `myagent docs show <doc>` 里定位到原文

## 7.5 CLI

```bash
myagent research ingest data/papers/*.pdf
myagent research chat --session work
myagent research ask "这篇论文解决了什么问题？" --paper <document_id>
myagent search "GraphRAG" -k 5
```

## 阶段产出

```text
examples/research_agent/
├── agent.py
├── tools.py
├── prompts.py
└── README.md
docs/research-agent.md
docs/records/phase-7-research-agent.md   # 4 个 Demo 的 transcript + token / 延迟
```

## 验收标准（4 个 Demo）

* [ ] **Demo 1 单论文问答**：同一篇论文追问 3 个问题，答案均带引用，人工判定正确
* [ ] **Demo 2 多论文比较**：`document_ids` 过滤生效，回答同时引用 ≥ 2 篇论文
* [ ] **Demo 3 跨 Session Memory**：Session A 建立「研究方向 / 偏好」，新 Session 提问能召回
* [ ] **Demo 4 RAG + Tool Calling**：一次对话内既检索论文，又调用 `save_note` / `search_memory`
* [ ] 每个 Demo 记录轮数、工具调用次数、token、延迟（Phase 8 直接复用）
* [ ] 拒答场景：问知识库里没有的内容时，明确回答「知识库中没有」，不编造事实

---

# Phase 8：Evaluation

## 目标

用**可复现的离线评测**证明改造确实有效：Memory 有用、RAG 有用、参数选择有依据。
关键不是分数好看，而是「同一数据集、同一套指标、能一键复跑」。

前置：Phase 5 的 pipeline、Phase 6 的开关与 `ContextReport`、Phase 7 的 4 个 Demo 场景。

## 8.1 数据集

```text
evaluation/
├── dataset/questions.jsonl     # 全部题目
└── dataset/papers/             # 题目引用的 PDF（或指向 data/papers 的软链）
```

```json
{"id": "q001", "type": "single_paper", "question": "...", "answer": "...",
 "gold_chunk_ids": ["<document_id>:3"], "source": "papers/x.pdf"}
```

* 20～50 条，覆盖四类：`single_paper` / `compare` / `memory` / `refusal`（库里没有，应当拒答）
* 拆 `dev`（调参）/ `test`（只跑一次出结论），避免过拟合到指标（记 ADR-0011）
* `gold_chunk_ids` 只标注**能回答该问题的 chunk**，用于检索指标；`answer` 用于人工 / LLM-judge 判分

## 8.2 指标

| 层级 | 指标 | 定义 | 数据来源 |
| --- | --- | --- | --- |
| Retrieval | `hit@k` | top-k 里是否含任一 `gold_chunk_id` | `search_paper` 返回 |
| Retrieval | `MRR@k` | 第一个 gold chunk 的倒数排名 | 同上 |
| Retrieval | `p50/p95 latency` | 检索耗时 | 结构化日志（Phase 9） |
| Memory | `memory_hit@k` | 记忆题是否召回正确记忆 | `search_memory` 返回 |
| Answer | `accuracy` | LLM-judge 1–5 分（≥4 记正确）+ 人工抽检 20% | `evaluation/judge.py` |
| Answer | `citation_rate` | 带有效引用的回答占比 | 正则解析 `[doc#idx]` |
| Agent | `avg_turns / avg_tool_calls / avg_tokens / avg_latency` | 每题均值 | `AgentRunResult` + 日志 |
| Context | `request_tokens` | 每轮请求 token | `ContextReport` |

* judge 使用固定 prompt + 与产出回答不同的模型；样本小就诚实标注「仅看趋势」

## 8.3 对比实验

每组一张表，全部写进 `docs/evaluation.md`：

| # | 变量 | 对照 | 观察指标 |
| --- | --- | --- | --- |
| 1 | Memory | `MYAGENT_MEMORY_ENABLED=true/false` | `memory_hit@5`、`accuracy`、`request_tokens` |
| 2 | RAG | `MYAGENT_RAG_ENABLED=true/false` | `hit@5`、`accuracy`、拒答题误答率 |
| 3 | Top-K | 3 / 5 / 10 | `hit@k`、`latency`、`request_tokens` |
| 4 | Chunk size | 400 / 800 / 1200 | `hit@5`、平均 chunk 长度 |
| 5 | Reranker | Identity / Score /（可选）模型 | `hit@3`、`latency` |
| 6 | 压缩 | compact ON/OFF | `request_tokens`、探针问题正确率 |

## 8.4 框架

```python
# evaluation/evaluate.py
async def run_suite(suite: str, config: EvalConfig) -> EvalReport: ...
```

* 固定随机种子；LLM 输出按 `(question_id, config_hash)` 缓存到 `evaluation/.cache/`，保证可复跑且省钱
* `--offline` 模式用 mock provider，验证评测代码本身能在 CI 里跑

## 阶段产出

```text
evaluation/
├── dataset/questions.jsonl
├── metrics.py        # hit@k / MRR / accuracy / citation_rate
├── judge.py          # LLM-judge（固定 prompt + 独立模型）
├── evaluate.py       # run_suite / 缓存 / 报告
├── results.json      # 每次运行的原始结果
└── README.md
docs/evaluation.md
docs/decision-records/0011-evaluation-protocol.md
```

## 验收标准

* [ ] `python evaluation/evaluate.py --suite all --out evaluation/results.json` 一键跑完并可复跑（结果稳定）
* [ ] 6 组对比都有 delta 表，且每条结论都能指到 `results.json` 的具体字段
* [ ] `evaluation/evaluate.py --offline` 在 CI 里通过（不打网络）
* [ ] `docs/evaluation.md` 能直接用数据回答：

> 你的 Memory 为什么有效？（`memory_hit@5` 与 `accuracy` 的 delta）

> RAG 加入以后有什么变化？（`hit@5` / `accuracy` / 拒答题误答率）

> Retrieval 的 Top-K 为什么选择这个值？（`hit@k` 与 `latency`、`request_tokens` 的权衡）

> Chunk size / reranker 的取舍依据是什么？

---

# Phase 9：工程化

## 目标

让项目从「能跑的学习项目」变成「别人能装、能跑、能测的工程项目」。

前置：Phase 1–8 的功能已可用；已有质量门 `scripts/check.sh`（format → lint → type → test + coverage）。

## 9.1 结构化日志

在 Phase 0 的 `src/myagent/observability/logging.py`（命名空间 `myagent.*`、text / JSON 双格式）
之上，定义**统一事件名 + 字段表**，而不是散落的 `print` 或 `logger.info("...")`：

| 事件 | 时机 | 关键字段 |
| --- | --- | --- |
| `agent.turn.start` / `agent.turn.finish` | Loop 的一轮 turn 前后 | `session_key`, `stop_reason`, `turns`, `latency_ms` |
| `llm.call` | 每次模型请求 | `model`, `input_tokens`, `output_tokens`, `latency_ms` |
| `tool.call` | 每次工具执行 | `tool`, `ok`, `latency_ms`, `concurrency_batch` |
| `memory.write` / `memory.search` | 记忆写入 / 检索 | `kind`, `count`, `top_score` |
| `rag.ingest` / `rag.retrieve` | 入库 / 检索 | `documents`, `chunks`, `top_k`, `top_score` |
| `context.build` | 每次组装上下文 | `budget_tokens`, `used_tokens`, `dropped_tokens`, `compacted` |

* 事件与字段的形态参考上游把压缩做成结构化事件的做法（`events.py:17` 的 `ContextCompactionEvent`）
* 用 `extra={...}` 传字段，JSON 模式下可被 `jq` 直接筛选
* [ ] 日志**不打印密钥**，也不打印完整文档正文（只记 id 与长度）
* [ ] 能用一条命令从 JSON 日志重建「一次 turn 的事件时间线」

## 9.2 配置

* **保留 `.env` 为唯一真源**（ADR-0004）；新增 `.env.example`（占位值、不含密钥）与
  `docs/configuration.md`（`LLM_*` / `EMBED_*` / `MYAGENT_*` 全部配置项、默认值、是否必填）
* `myagent config show`：打印解析后的配置（密钥打码）
* `myagent config check`：启动自检——SQLite 可写、Qdrant 可达、embedding 可调用、collection 维度一致
* 原计划里的 YAML 配置**不再引入**：多一套配置格式意味着两处真源，与 ADR-0004 冲突。
  若确需 dev/prod 覆盖，用 `--config-file <toml>` 覆盖层，并记 **ADR-0012** 说明边界

## 9.3 Docker

```text
Dockerfile              多阶段构建（builder 装依赖，runtime 只带 venv + src）
docker-compose.yml      services: qdrant + app（app 挂载 ./data 与 .env）
```

```bash
docker compose up -d qdrant
docker compose run --rm app myagent config check
docker compose run --rm app myagent ingest data/papers/*.pdf
```

* 对齐上游交付形态（`nanobot/Dockerfile`、`nanobot/docker-compose.yml`），但不搬运其频道 / WebUI 相关服务
* [ ] 镜像里不含密钥；`data/` 与 `.env` 通过 volume / 环境注入

## 9.4 测试与质量门

```text
tests/
├── test_agent.py
├── test_tools.py
├── test_memory.py
├── test_rag.py
├── test_context.py
├── test_models.py
├── test_settings.py
├── rag/{test_loader,test_chunker,test_store,test_pipeline}.py
└── smoke/{test_llm_smoke,test_qdrant_smoke}.py    # 默认跳过
```

* 默认 `pytest -m "not smoke"`：全部离线（假 provider / 假 embedder / 内存向量库 / 临时 SQLite）
* 覆盖率门：整体 ≥ 75%；核心模块（`agent/runner.py`、`tools/`、`agent/context.py`、
  `memory/sqlite_store.py`）≥ 90%
* `scripts/check.sh` 加上 `pytest -m "not smoke"` 与覆盖率阈值断言；pre-commit 覆盖 `examples/`、`evaluation/`
* CI：`.github/workflows/ci.yml` 在 push / PR 上跑 `scripts/check.sh`

## 阶段产出

```text
Dockerfile
docker-compose.yml
.env.example
.github/workflows/ci.yml
docs/configuration.md
docs/decision-records/0012-configuration-source.md
docs/records/phase-9-engineering.md
```

## 验收标准

* [ ] 干净环境按 README 走通：`docker compose up -d qdrant` → `myagent config check` →
      `myagent ingest` → `myagent research ask "..."` 全部成功
* [ ] `scripts/check.sh` 全绿且覆盖率达标
* [ ] CI 在 push 上跑通
* [ ] JSON 日志能选出一条完整 turn 的事件序列（README 里给出 `jq` 命令）
* [ ] 仓库里不存在真实密钥（`git grep` 检查 + `.gitignore` 覆盖 `.env`、`data/`）

---

# Phase 10：README / Demo / 简历包装

## 目标

把工程变成「5 分钟能看懂、能跑起来、能记住亮点」的作品。所有宣称都必须能找到证据。

## 10.1 README 结构

```text
# MyAgent — Modular Agent Runtime & Research Agent

## Introduction        一段话：是什么、为什么做、与 nanobot 的关系（学习 + 重构，非 fork）
## Features            能力清单，每条链接到对应文档
## Architecture        系统图 + 一轮请求时序（Phase 1 的图 + Phase 3 的装配图）
## Quick Start         .env → docker compose up → ingest → chat（可复制粘贴的命令）
## Agent Runtime       Loop / Runner / Tool Registry / Model Provider → docs/agent-loop.md、docs/tool-system.md
## Memory              分层记忆、写入策略、检索 → docs/memory-design.md
## RAG                 Pipeline、chunk 策略、检索参数 → docs/rag-design.md
## Context             优先级 / 预算 / 压缩 → docs/context-design.md
## Research Agent      4 个 Demo + GIF → docs/research-agent.md
## Evaluation          指标表 + 关键结果 → docs/evaluation.md、evaluation/results.json
## Project Structure   目录树
## Design Decisions    ADR 索引（0001–0012，每条一句话结论）
## Limitations         诚实写清：不做多模态、不联网抓论文、评测样本规模有限
## Future Work         §7 的可选方向（Hybrid Retrieval / Query Rewrite / Consolidation …）
## Acknowledgements    上游 nanobot 与许可说明
```

## 10.2 架构图

README 顶部要有两张图（mermaid 或导出 PNG 均可）：

```text
图 1：系统组成
                     User
                      │
                      ▼
                ┌───────────┐     ┌──────────────────┐
                │ AgentLoop │────▶│ ContextManager   │
                └─────┬─────┘     └────────┬─────────┘
                      │                    │
              ┌───────▼───────┐     ┌──────┴───────┬──────────┐
              │  AgentRunner  │     ▼              ▼          ▼
              └───────┬───────┘   Memory         RAG        Tools
                      ▼              │            │           │
                     LLM ◀───────────┴────────────┴───────────┘
                      │
                      ▼
                  Response

图 2：一轮请求（build → run → save → respond）里 Memory / RAG / Tools 的介入点
```

## 10.3 Demo

3 分钟脚本（录成 GIF + 视频）：

```text
1. myagent config check                          # 环境自检
2. myagent ingest data/papers/*.pdf              # 建库（展示 chunks 数）
3. myagent research ask "这篇论文解决了什么问题？"   # 单篇 + 引用
4. myagent research ask "对比 A 与 B 的方法"       # 多篇比较
5. myagent memory list                           # 展示被保存的偏好 / 结论
6. myagent research chat --session new           # 新会话
7. 问「我最近在研究什么方向？」                      # 跨 Session 记忆召回
8. 打开 docs/evaluation.md 展示指标表              # 量化结果
```

## 10.4 技术亮点

每条亮点都要指向证据，不写没有依据的形容词：

```text
✓ Modular Agent Runtime      → docs/design.md（Protocol + 装配注入，替换实现零改动）
✓ Tool Calling               → docs/tool-system.md（schema 校验 + 并发分批 + 错误语义）
✓ Layered Memory             → docs/memory-design.md（写入过滤 + 去重 + 巩固）
✓ Long-term Memory Retrieval → docs/evaluation.md（memory_hit@5 / accuracy 的 delta）
✓ RAG Pipeline               → docs/rag-design.md（幂等 ingest + 引用可回跳）
✓ Context Management         → docs/context-design.md（优先级 / 预算 / 压缩实验）
✓ Vertical Agent             → docs/research-agent.md（4 个 Demo）
✓ Evaluation                 → evaluation/results.json（6 组对比）
```

## 10.5 简历条目

每条都要带**可验证的数字**（取自 `results.json`），不要形容词：

```text
Framework   重新实现轻量模块化 Agent Runtime（Loop / Runner / Tool Registry / Model Provider /
            Session / Context Manager），通过 Protocol + 装配注入解耦，替换模型/工具/存储实现无需改业务代码。
Memory      设计 Working / Episodic / Semantic 三层记忆，实现写入过滤（去重 + 重要度阈值）、
            时间衰减检索与 Episodic→Semantic 巩固；跨 Session 记忆题 hit@5 = xx%，
            相比关闭记忆准确率 +xx pt。
RAG         构建模块化 RAG Pipeline（Loader / Chunker / Embedder / VectorStore / Retriever / Reranker），
            基于 Qdrant + DashScope embedding，支持内容寻址幂等入库与按文档过滤检索；
            chunk 400/800/1200 对比后选定 xxx，hit@5 = xx%。
Application 基于自研 Runtime 实现 Research Agent（论文问答 / 多论文比较 / 跨会话记忆 / 工具调用），
            并建立离线 Evaluation Pipeline，覆盖检索、记忆、回答与成本四类指标。
```

## 阶段产出

```text
README.md（重写）
docs/architecture.md（更新为最终版）
docs/decision-records/（0001–0012 索引）
docs/demo/（GIF / 视频脚本 / 截图）
docs/records/phase-10-package.md    # 发布快照：tag / commit / 指标
```

## 验收标准

* [ ] 陌生读者只看 README，5 分钟内能在干净环境跑通 `config check → ingest → ask`
* [ ] README 里每条「亮点」都能点到一个文档章节或 `results.json` 字段（没有无证据的形容词）
* [ ] Demo GIF 展示完 10.3 的 8 步，可离线播放
* [ ] `docs/records/phase-10-package.md` 记录发布时的 commit / tag 与关键指标
* [ ] ADR 索引能覆盖 §8 原则三里列出的所有「为什么这样设计」问题

---

# 4. 最终项目目录

最终形态（仓库根即工程根，见 ADR-0001）：

```text
kyobot/
│
├── README.md  PLAN.md  LICENSE
├── pyproject.toml             # hatchling + ruff / mypy / pytest 配置
├── Dockerfile  docker-compose.yml
├── .env.example  .pre-commit-config.yaml  .gitignore
│
├── src/myagent/
│   ├── agent/                 # loop.py / runner.py / context.py / session.py / runtime.py / types.py
│   ├── models/                # base.py（BaseModel）/ openai_compat.py
│   ├── tools/                 # base.py / registry.py / builtin/
│   ├── memory/                # types / base / manager / working / episodic / semantic /
│   │                          # extractor / retriever / consolidator / sqlite_store / vector_index
│   ├── rag/                   # types / loader / chunker / embedder / vectorstore /
│   │                          # retriever / reranker / store / pipeline
│   ├── session/               # manager.py（JSONL + 摘要检查点）
│   ├── config/                # env.py / settings.py / schema.py
│   ├── observability/         # logging.py（text / JSON 双格式）
│   ├── runtime.py             # build_agent()：唯一装配入口
│   └── cli.py                 # chat / tools / ingest / search / docs / memory / research / config
│
├── examples/research_agent/   # agent.py / tools.py / prompts.py / README.md
│
├── evaluation/                # dataset/ metrics.py judge.py evaluate.py results.json README.md
│
├── tests/                     # test_*.py + rag/ + smoke/（默认跳过）
│
├── scripts/                   # bootstrap.sh / check.sh / check_doc_anchors.py
│
├── docs/
│   ├── architecture.md · agent-loop.md · tool-system.md · context.md · memory.md   # Phase 1
│   ├── design.md · memory-design.md · rag-design.md · context-design.md            # Phase 3–6
│   ├── research-agent.md · evaluation.md · configuration.md                        # Phase 7–9
│   ├── records/               # phase-0 … phase-10 工作记录
│   └── decision-records/      # ADR-0001 … 0012
│
└── nanobot/                   # 上游只读参照（git ignored，见 ADR-0002）
```

---

# 5. 每阶段核心产出

| 阶段 | 核心产出 | 验收标准（可判定） |
| --- | --- | --- |
| Phase 0 | 开发环境 + 仓库骨架 | `scripts/bootstrap.sh` 可复现；nanobot 跑通 |
| Phase 1 | 5 份带行号锚点的源码文档 | `scripts/check_doc_anchors.py` 全绿；能讲清一次请求 |
| Phase 2 | Framework V1 | 删除 `nanobot/` 后 `myagent chat` 仍可对话 + 工具调用 |
| Phase 3 | Framework V2（可注入 / 可替换） | 换模型只改 `.env`；Runner 不 import 具体实现 |
| Phase 4 | 分层 Memory | 跨 Session 召回 + 写入/去重/巩固三类实验表 |
| Phase 5 | RAG Pipeline | 幂等 ingest + 带引用回答 + 维度探测 |
| Phase 6 | Context Manager | 四来源预算裁剪 + 压缩实验 + 开关实验 |
| Phase 7 | Research Agent | 4 个 Demo 全部可复现 |
| Phase 8 | Evaluation | 一键复跑 + 6 组对比 delta 表 |
| Phase 9 | 工程化 | `docker compose` 全流程 + CI + 覆盖率门 |
| Phase 10 | 项目包装 | README 5 分钟上手 + Demo + 指标可追溯 |

---

# 6. MVP 版本定义

如果时间不足，不要继续增加功能。

优先完成以下 MVP：

```text
                       MyAgent
                          │
           ┌──────────────┼──────────────┐
           ▼              ▼              ▼
        Agent          Memory           RAG
           │              │              │
           ▼              ▼              ▼
      Tool Calling    Long-term       PDF Search
           │           Memory             │
           └──────────────┼───────────────┘
                          ▼
                   Context Manager
                          │
                          ▼
                   Research Agent
                          │
                          ▼
                     Evaluation
```

MVP = Phase 0–8 的最小交集，每项都要有可判定的完成条件：

* [x] Agent Loop + Runner（会话内串行、`max_iterations`、工具错误回灌）— Phase 2 已完成
      （`src/myagent/agent/loop.py:281` 会话锁、`src/myagent/agent/runner.py:98` 迭代上限）
* [x] Tool Calling（schema 校验 + 并发分批 + 错误语义）— Phase 2 已完成
      （`src/myagent/tools/base.py:238` 校验、`src/myagent/agent/runner.py:220` 并发分批）
* [ ] Session（JSONL + `last_archived` 边界 + 摘要检查点）— Phase 3 已把契约与 JSONL 实现拆开
      （`src/myagent/session/base.py:44`），摘要检查点留给 Phase 4/6
* [ ] Memory（Episodic/Semantic 分开、写入过滤、向量召回）
* [ ] Vector Retrieval（Qdrant + DashScope embedding，按文档过滤）
* [ ] RAG（PDF → chunk → embedding → 带引用回答）
* [ ] Context Manager（优先级 + 预算 + 结构修复 + 压缩）
* [ ] Research Agent（4 个 Demo）
* [ ] 基础 Evaluation（≥ 6 组对比、可复跑）

Nice to Have（不阻塞 MVP）：Hybrid Retrieval、Query Rewrite、模型型 Reranker、定时巩固、
MCP / 多频道 / WebUI、Multi-Agent。

判定口径：MVP 完成 = 以上 9 项全部勾选，且 `scripts/check.sh` 全绿、
`evaluation/results.json` 含 ≥ 6 组对比。

---

# 7. 后续可选方向

在 MVP 完成以后，再考虑：

## 7.1 Hybrid Retrieval

```text
BM25
+
Vector Search
↓
Fusion
↓
Rerank
```

## 7.2 Query Rewrite

```text
User Query
 ↓
Query Rewriter
 ↓
Retriever
```

## 7.3 Memory Consolidation

```text
Episodic Memory
       ↓
Memory Summarizer
       ↓
Semantic Memory
```

## 7.4 Agent Observability

> Phase 9.1 已交付「事件名 + 字段表」的结构化日志；这一节指的是在它之上的可视化 Trace 视图（时间线 / 瀑布图），
> 属于 MVP 之后的增强。

增加：

```text
Trace
 ├── LLM Call
 ├── Tool Call
 ├── Memory Retrieval
 ├── RAG Retrieval
 └── Token Usage
```

## 7.5 Multi-Agent

只有在单 Agent Runtime 稳定以后再考虑：

```text
Research Agent
      │
      ├── Search Agent
      ├── Paper Agent
      └── Writer Agent
```

---

# 8. 项目开发原则

## 原则一：不要追求功能数量

项目核心不是：

```text
10 个 Agent Feature
```

而是：

```text
3～5 个核心模块
+
清晰的架构
+
真实的技术改造
+
Evaluation
```

## 原则二：不要直接复制整个 nanobot

采用：

```text
理解
 ↓
迁移
 ↓
重新抽象
 ↓
功能增强
 ↓
测试
```

而不是：

```text
Fork
 ↓
改名
 ↓
改几个文件
```

## 原则三：每次重构都要有理由

例如：

```text
为什么 Memory 独立？

为什么 ContextManager 独立？

为什么 Retriever 抽象？

为什么 Tool 使用 Registry？

为什么 Memory 和 RAG 分开？

为什么需要 Session？

为什么需要 AgentRunner？
```

这些问题最终都会成为面试问题。

## 原则四：优先保证可解释性

整个项目最终应该能够被你用一张架构图解释清楚。

## 原则五：所有核心改动都要有实验

例如：

```text
Memory ON / OFF
RAG ON / OFF
Top-K 3 / 5 / 10
不同 Chunk Size
不同 Retrieval Strategy
```

---

# 9. 最终简历项目定位

项目名称：

> **MyAgent —— Modular Agent Runtime & Research Agent**

项目定位：

> 面向知识密集型任务的轻量级 Agent Framework。

核心技术：

```text
Python
LLM
Agent Runtime
Tool Calling
RAG
Vector Retrieval
Long-term Memory
Context Management
Evaluation
```

简历项目描述建议最终围绕以下三个方向：

### Framework

> 重新设计并实现轻量级模块化 Agent Runtime，抽象 Agent Loop、Agent Runner、Tool Registry、Model Provider、Session 和 Context Manager，实现模型调用、工具编排及上下文管理解耦。

### Memory / RAG

> 设计分层 Memory Architecture，将 Working、Episodic 和 Semantic Memory 解耦，并基于向量检索实现长期记忆召回；同时构建模块化 RAG Pipeline，支持文档解析、Chunk、Embedding、Vector Retrieval 和 Reranking。

### Application / Evaluation

> 基于自研 Agent Runtime 构建 Research Agent，实现论文知识库问答、跨 Session Memory、Tool Calling 和多文档检索，并建立离线 Evaluation Pipeline，从 Retrieval Hit Rate、Answer Accuracy 和 Latency 等指标评估系统效果。

---

# 10. 最终完成标准

当以下问题你都能够回答时，项目基本达到简历项目要求：

### Agent

* [ ] Agent Loop 是什么？
* [ ] Agent Runner 和 Agent Loop 为什么分开？
* [ ] Tool Calling 如何实现？
* [ ] 如何限制 Agent 无限循环？
* [ ] Tool Error 如何处理？

### Context

* [ ] Context 是怎么构建的？
* [ ] Conversation、Memory、RAG 如何进入 Context？
* [ ] Context 太长怎么办？
* [ ] 如何进行 Context Compression？

### Memory

* [ ] Short-term Memory 和 Long-term Memory 有什么区别？
* [ ] Episodic Memory 和 Semantic Memory 为什么要区分？
* [ ] 什么信息应该写入 Memory？
* [ ] Memory 如何检索？
* [ ] 如何避免 Memory 污染？

### RAG

* [ ] 为什么需要 Chunk？
* [ ] Chunk Size 怎么选择？
* [ ] Embedding 做什么？
* [ ] Vector Search 怎么工作？
* [ ] Top-K 怎么选择？
* [ ] Retriever 和 Reranker 有什么区别？
* [ ] RAG 为什么会召回错误内容？

### Framework

* [ ] 为什么需要 BaseModel？
* [ ] 为什么 Tool 需要 Registry？
* [ ] 为什么 RAG 不直接写在 AgentLoop？
* [ ] 如何扩展新的 Model？
* [ ] 如何扩展新的 Tool？
* [ ] 如何扩展新的 Memory Store？

### Engineering

* [ ] 如何测试 Agent？
* [ ] 如何评价 RAG？
* [ ] 如何评价 Memory？
* [ ] 如何降低 Token 消耗？
* [ ] 如何降低 Agent Latency？

---

# 11. 项目最终目标

最终不是为了证明：

> “我改过 nanobot。”

而是为了证明：

> **我理解一个 Agent Framework 的核心运行机制，并能够从现有开源实现中提取核心设计，重新进行模块抽象，在此基础上实现 Memory、RAG 和 Context 等能力，并最终将 Framework 应用于实际垂直场景。**

项目最终形成：

```text
          nanobot
             │
             │ 学习 / 迁移
             ▼
      My Agent Framework
             │
      ┌──────┼───────┐
      ▼      ▼       ▼
    Agent  Memory    RAG
      │      │       │
      └──────┼───────┘
             ▼
       Context Manager
             │
             ▼
            LLM
             │
             ▼
      Research Agent
             │
             ▼
        Evaluation
             │
             ▼
     可展示的工程项目
```

**最终评价项目的标准不是代码量，而是：架构是否清晰、核心模块是否真正理解、改造是否有技术理由、实验是否能够验证设计，以及你能否完整讲清楚整个 Agent Runtime。**
