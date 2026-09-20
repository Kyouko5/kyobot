# ADR 0006：Phase 2 新增依赖的理由

- 状态：已接受
- 日期：2026-09-20
- 关联：Phase 2.1（Framework 目录）、Phase 2.2（Model 抽象）、Phase 2.6（CLI）；
  实现入口 `src/myagent/models/openai_compat.py`、`src/myagent/cli.py`

## 背景

Phase 0 结束时运行时依赖只有 `python-dotenv`。Phase 2 需要两件新东西：

1. 一个能真正调模型的客户端：本项目的 LLM 与 Embedding 都走「OpenAI 兼容端点」
   （ADR-0005），需要处理 HTTP 超时、连接错误、限流、错误分类这些真实世界的细节。
2. 一个命令行入口：`myagent chat` / `myagent tools`，三个子命令、纯文本输出。

`docs/development.md` 的约定是：**运行时依赖越少越好，每增加一个都要有决策记录**。

## 决策

1. **引入 `openai>=1.50`（AsyncOpenAI）** 作为唯一的模型客户端实现，
   由 `OpenAICompatModel` 包一层（`src/myagent/models/openai_compat.py:62`）。
2. **CLI 使用标准库 `argparse`**，不引入 typer / click / rich
   （`src/myagent/cli.py:61`）。
3. 该依赖的作用域被限制在 `myagent.models.openai_compat`：框架其余部分只认
   `BaseModel` 协议（`src/myagent/models/base.py:71`）与 `LLMError` / `ContextWindowExceeded`
   （`src/myagent/models/base.py:31`、`src/myagent/models/base.py:35`）——`openai` 的异常类型
   不会泄漏到 runner / loop。

## 理由

- **一个客户端覆盖所有提供方**：DashScope（阿里云 MaaS 兼容模式）、DeepSeek、OpenAI 都用同一套
  `/chat/completions` 线格式，换提供方只改 `LLM_BASE_URL` / `LLM_MODEL`。
- **省掉手写 HTTP 客户端**：直接用 `httpx` 意味着自己实现超时、重试、错误体解析与 SSE 解析；
  这些细节与项目「理解 Agent Runtime」的目标无关，却会占掉 Phase 2 的大部分时间。
- **错误分类已经现成**：SDK 给出的 `APITimeoutError` / `AuthenticationError` /
  `RateLimitError` / `BadRequestError` 正好对应框架需要的几个错误语义，
  `_translate_error` 只需做一次映射（`src/myagent/models/openai_compat.py:123`）。
- **CLI 不值得引入框架**：交互循环 + 3 个子命令，argparse 已经够用；
  把 typer/rich 排除在外可以让 `pip install` 的传递依赖保持可解释。

## 后果

- 传递依赖变多（`pydantic`、`httpx` 等）：这是唯一新增运行时依赖的代价，记录在此。
- `openai` 的大版本升级可能改变异常层次：错误映射集中在 `_translate_error` 一个函数里，
  升级时只需要改这一处并跑 `tests/test_models.py`（用假客户端覆盖了 8 类错误）。
- 未来接入原生协议（Anthropic Messages API 之类）时，新增一个 `BaseModel` 实现即可，
  runner / loop 不需要改动；`LLM_PROVIDER` 已经是可扩展的枚举
  （`src/myagent/config/settings.py:77`）。

## 备选方案

| 方案 | 不采用的原因 |
| --- | --- |
| 直接用 `httpx` 手写客户端 | 需要自己做超时/重试/错误体解析，收益低、易出错 |
| `litellm` | 抽象层过厚，会吞掉错误细节，与我们「把 Runtime 讲清楚」的目标冲突 |
| `requests` | 只有同步 API，与 Agent Loop 的 `async` 结构不匹配 |
| typer / click + rich | 3 个子命令的收益不明显，却增加 2～3 个运行时依赖 |
