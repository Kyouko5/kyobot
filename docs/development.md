# 开发规范

Phase 0 的产出之一：把「怎么做」（而不是「做什么」）固定下来，后续每个阶段都按同一套标准交付。
规范里每条规则都给出理由，方便在面试里解释取舍。

## 1. 环境与依赖

```bash
scripts/bootstrap.sh              # 建 .venv + 安装 dev 依赖
source .venv/bin/activate
scripts/check.sh                  # 质量门
```

- Python 版本：`>=3.11`（本地 3.12.5）。`requires-python` 与 ruff `target-version = py311` 保持一致。
- 本机 python.org 的 3.12 框架版**没有自带 CA 证书包**，直接 `pip install` 会报
  `CERTIFICATE_VERIFY_FAILED`。`scripts/bootstrap.sh` 在这种情况下自动导出
  `SSL_CERT_FILE=/etc/ssl/cert.pem`（macOS 系统信任库）。
- **依赖管理策略**：runtime 依赖默认是零（Phase 0 的 `dependencies = []`）。新增依赖必须满足两条：
  （1）写清为什么标准库不够；（2）记入当阶段的工作记录或 ADR。开发期工具统一放在
  `[project.optional-dependencies].dev`，不进入运行时依赖。

## 2. 代码规范

| 规则 | 工具 / 位置 | 理由 |
| --- | --- | --- |
| 代码风格（格式） | `ruff format`（black 兼容） | 只保留一个格式化器，避免 black / ruff 互相改格式 |
| 静态检查 | `ruff check`，规则集 `E,F,W,I,N,UP,B,C4,SIM,RUF,ASYNC,ANN` | 覆盖命名、导入顺序、现代语法、常见 bugbear 与异步误用 |
| 行宽 | 100（`line-length = 100`） | 与上游 nanobot 一致，迁移过来的代码不需要重排 diff |
| 类型标注 | 所有函数签名必须标注；`mypy --strict` 覆盖 `src/` | 接口即契约；`py.typed` 让下游能享受类型信息 |
| 文档字符串 | 公共 API 必须写清契约（Args / Returns / Raises） | 解释「为什么」而不是复述「做了什么」 |
| 日志 | 库代码禁止 `print`，用 `get_logger()` | 输出格式由调用方决定，库不应该抢 stdout |

命名约定：模块与函数用 `snake_case`，类用 `PascalCase`，常量用 `UPPER_SNAKE_CASE`，
内部实现用前导下划线。测试目录放宽注释类规则（见 `pyproject.toml` 的 `per-file-ignores`）。

## 3. Logging 约定

实现见 `src/myagent/observability/logging.py`，三条规则：

1. **只写 `myagent.*` 命名空间**：框架不碰 root logger，避免影响宿主应用的日志配置。
2. **库不配置日志**：模块内部只调用 `get_logger(__name__)`；`configure_logging()` 只允许出现在
   应用入口（CLI / 脚本 / 测试）里。
3. **结构化字段用 `extra=`**：`get_logger("rag").info("indexed", extra={"doc_id": ..., "chunks": ...})`，
   配合 `configure_logging(fmt="json")` 直接产出一行一条 JSON —— Phase 8 的 Evaluation 需要可机读日志。

日志级别语义：

| 级别 | 用法 |
| --- | --- |
| `DEBUG` | 内部状态、流式 delta、上下文预算等细节 |
| `INFO` | 生命周期事件：turn 开始/结束、工具调用、索引完成 |
| `WARNING` | 可恢复的降级（重试、跳过某个来源、截断） |
| `ERROR` | 本轮失败且需要人看的事件，必须带异常信息 |

## 4. 测试规范

- 只放 `tests/` 下，命名 `test_*.py`；`pytest` 配置 `testpaths = ["tests"]`、`pythonpath = ["src"]`
  （不装包也能跑）。
- `asyncio_mode = "auto"`：异步测试直接写 `async def test_...`，不用手动加 marker（Phase 2 起大量用到）。
- 命名格式 `test_<行为>_<条件>`，一个测试只验证一个行为；参数化用 `@pytest.mark.parametrize`。
- 测试不得写真实 home / 真实网络：用 `tmp_path`、`monkeypatch`；对全局状态（logging、session 目录）
  必须提供恢复用的 fixture（参考 `tests/conftest.py`）。
- 覆盖率：Phase 0～8 用 `scripts/check.sh` 观察，Phase 9 起设定下限（对齐上游的 75%）并写进 CI。

## 5. Git 规范

采用 [Conventional Commits](https://www.conventionalcommits.org/)：

```text
<type>(<scope>): <subject>

[optional body: 为什么这么改]
[optional footer: 关联的 phase 或 ADR]
```

| type | 用途 |
| --- | --- |
| `feat` | 新增能力（如 `feat(memory): add episodic store`） |
| `fix` | 修 bug |
| `refactor` | 行为不变的整理（重构阶段主力） |
| `perf` | 性能优化（含 token / latency） |
| `test` | 测试新增或调整 |
| `docs` | 文档、ADR、阶段记录 |
| `build` / `ci` / `chore` | 打包、流水线、杂项 |

约定：

- `main` 始终可运行（`scripts/check.sh` 通过）；阶段开发走 `phase-<N>/<slug>` 分支。
- 提交粒度：**一个 Task 至少一次提交**，不允许阶段末一次性大批量提交（否则无法回溯决策）。
- 提交前跑 `scripts/check.sh`；pre-commit 只做快速检查，mypy 与 pytest 由质量门负责。
- 禁止提交：`nanobot/`（上游参照）、`.venv/`、任何密钥、数据集大文件 —— 已由 `.gitignore` 覆盖。

## 6. 质量门

```bash
scripts/check.sh          # ruff format --check → ruff check → mypy → pytest --cov
scripts/check.sh -k rag   # 额外参数透传给 pytest
```

可选的提交前钩子（只做快检，不替代质量门）：

```bash
source .venv/bin/activate
pre-commit install
pre-commit run --all-files
```

`.pre-commit-config.yaml` 里的 `mypy` 钩子使用 `language: system`，因此必须在已激活的虚拟环境里运行。

## 7. 文档规范

| 文档 | 位置 | 内容 |
| --- | --- | --- |
| 计划与验收标准 | `PLAN.md` | 唯一的路线入口 |
| 架构文档 | `docs/architecture.md`、后续 `docs/*-design.md` | 模块边界、消息流、接口 |
| 决策记录（ADR） | `docs/decision-records/NNNN-<slug>.md` | 一个决策一份，含备选方案与后果 |
| 阶段记录 | `docs/records/phase-<N>-<slug>.md` | 见下方模板 |

阶段记录模板（沿用计划书的证据原则：结论必须可复现）：

```markdown
# Phase N 工作记录：<阶段名>

- 日期：YYYY-MM-DD ~ YYYY-MM-DD
- 状态：已完成 / 部分完成（遗留问题见末尾）
- 关联提交：<commit hash> <message>

## 1. 阶段目标
## 2. 问题定义
## 3. Baseline（改造前行为与指标 + 测量方式）
## 4. 方案设计（架构图 / 接口 / 取舍）
## 5. 实现（文件清单与职责）
## 6. 实验设置（数据、样本量、指标、命令）
## 7. 结果（命令输出 / 数据）
## 8. 结论与遗留问题
```
