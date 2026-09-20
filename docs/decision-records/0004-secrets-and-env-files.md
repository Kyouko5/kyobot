# ADR 0004：密钥与环境变量统一走 .env + load_dotenv()

- 状态：已接受
- 日期：2026-09-20（项目前期确定）
- 关联：Phase 0（`src/myagent/config/env.py`、`.env`、`.env.example`）、Phase 2/5（LLM 与 Embedding key）

## 背景

项目需要 LLM key、Embedding key、Qdrant key 等多类密钥。如果每个模块各自读 `os.environ`，
既无法集中审计「到底读了哪些密钥」，也容易把 `.env` 与真实环境变量的优先级搞混。

## 决策

1. 仓库根目录维护 `.env`（**git ignored**，存真实值）与 `.env.example`（**提交**，只列键名与说明，
   值留空）。
2. 所有密钥读取都必须经过 `python-dotenv` 的 `load_dotenv()`：`myagent.config.env.load_env()`
   是唯一加载点，`get_env` / `require_env` / `get_bool_env` 是唯一读取点；其它模块不得用
   `os.environ` 直接取密钥。
3. `override=False`：真实环境变量优先于文件，方便 CI 与临时调试覆盖。
4. **空值等于未设置**：`.env` 中 `OPENAI_API_KEY=` 这类占位键不会让 `require_env` 误判为已配置。
5. `MYAGENT_ENV_FILE` 可指向别处的 `.env`（测试与多环境）。
6. 配置分层：`.env` → 类型化设置（`myagent.config.settings`）→ 具体实现（LLM client / Store 只接收设置对象）。

## 理由

- 单一入口让「哪些密钥被读取」可审计、可测试（`tests/test_env.py` 全部围绕这条路径）。
- 空值=未设置是 `.env` 作为模板的前提：模板必须在 key 未填时也能安全存在，缺 key 时应当明确报错。
- 环境变量优先使 CI、容器与临时覆盖都不需要改文件。
- 暂不引入 pydantic-settings：当前只有一个 dotenv 需求，标准库 dataclass + 少量校验函数足够；
  等 Phase 2 引入结构化 config 文件时再评估。

## 后果

- 新增第一条运行时依赖：`python-dotenv>=1.0`。
- `.env` 是进程级副作用：测试必须隔离 `os.environ`（`tests/test_env.py` 的 `isolated_environ`），
  并把 `MYAGENT_ENV_FILE` 指向临时文件，避免真实 `.env` 泄漏进测试。
- 密钥永不进日志与异常信息：错误只提变量名，不提值。
