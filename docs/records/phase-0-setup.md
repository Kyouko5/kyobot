# Phase 0 工作记录：项目准备

- 日期：2026-09-17
- 状态：已完成
- 关联提交：`<待提交>`（Phase 0 的产出尚未 commit，见文末遗留问题）

## 1. 阶段目标

建立开发环境、跑通 nanobot、建立自己的项目仓库与开发规范，并能回答计划书要求的两道验收问题。

## 2. 问题定义

| # | 问题 | 可验证的完成条件 |
| --- | --- | --- |
| 1 | 需要一份完全属于自己的代码库，而不是改包名的 fork | 仓库里没有上游源码；`nanobot/` 被 git 忽略 |
| 2 | 需要一套能支撑后续 10 个阶段的工程规范 | 一条命令即可跑完 lint / format / type / test |
| 3 | 需要能解释上游运行机制 | 能回答「nanobot 如何启动」「一条消息经过哪些模块」 |

## 3. Baseline

改造前的本仓库状态（`git status` 只有 `PLAN.md`、`.gitignore` 与两个空目录）：

- 无 `pyproject.toml`、无 `src/`、无 `tests/`、无 `docs/`、无脚本；没有 lint / type / test / logging 约定。
- 上游 nanobot 已可运行：`nanobot/.venv/bin/nanobot --version` → `🐈 nanobot v0.3.5`。
- 远端仓库已存在：`origin  https://github.com/Kyouko5/kyobot.git`。
- 环境：`python3 -m venv`（Python 3.12.5），配置文件 `~/.nanobot/config.json`（preset `deepseek-v4.1-flash`），
  工作区 `~/.nanobot/workspace`，会话存储在 `~/.nanobot/sessions/<workspace-id>/`。

## 4. 方案设计

四条决策，理由与备选方案见 ADR：

1. 仓库根目录即工程根，采用 src layout（`src/myagent/`）。
2. 格式化与静态检查统一 ruff，类型检查 `mypy --strict`，测试 pytest（`asyncio_mode = "auto"`）。
3. 质量门收敛为 `scripts/check.sh`；日志用标准库 `logging` 实现，只写 `myagent.*` 命名空间。
4. 运行时依赖保持为空，按阶段逐个引入。

验收问题的答案：`docs/architecture.md`（含启动装配顺序表与消息流步骤表，均带文件行号）。

## 5. 实现

| 文件 | 职责 |
| --- | --- |
| `pyproject.toml` | 打包（hatchling）、`[project.optional-dependencies].dev`、ruff / mypy / pytest / coverage 配置 |
| `.gitignore` | 排除 `nanobot/`（上游参照）、`.venv/`、缓存、本地状态与密钥 |
| `.editorconfig` | 统一的缩进、换行与行宽约定 |
| `.pre-commit-config.yaml` | 提交前快检：大文件、冲突标记、TOML/YAML、行尾、ruff、`mypy`（local hook） |
| `scripts/bootstrap.sh` | 建 venv、处理 macOS CA 证书问题、安装 dev 依赖 |
| `scripts/check.sh` | 质量门：`ruff format --check` → `ruff check` → `mypy` → `pytest --cov` |
| `src/myagent/__init__.py` | 包入口与 `__version__`（从安装元数据读取） |
| `src/myagent/py.typed` | 声明包内联类型信息 |
| `src/myagent/observability/logging.py` | `configure_logging` / `get_logger` / `reset_logging`、text 与 JSON 两种格式 |
| `src/myagent/observability/__init__.py` | 可观测性公共导出面 |
| `tests/conftest.py` | `myagent_logger` fixture：恢复全局 logging 状态 |
| `tests/test_logging.py` | 日志行为测试（12 项） |
| `tests/test_package.py` | 版本与导出面测试 |
| `README.md` | 项目定位、进度表、快速开始、目录结构、文档索引 |
| `docs/architecture.md` | Phase 0 验收材料：启动路径 + 消息流 + 迁移计划 |
| `docs/development.md` | 开发规范：环境、代码、日志、测试、Git、质量门、文档 |
| `docs/decision-records/0001-…`、`0002-…` | 布局与工具链、上游只读参照 |

## 6. 实验设置

在仓库根目录运行（`.venv` 已建好）：

```bash
scripts/bootstrap.sh
scripts/check.sh
PATH="$PWD/.venv/bin:$PATH" .venv/bin/python -m pre_commit run --files .pre-commit-config.yaml pyproject.toml \
  src/myagent/observability/logging.py tests/test_logging.py scripts/check.sh
```

指标：工具退出码、测试通过数、覆盖率（语句 + 分支）。

## 7. 结果

```text
== using .venv/bin/python ==
== ruff format --check ==   6 files already formatted
== ruff check ==            All checks passed!
== mypy ==                  Success: no issues found in 3 source files
== pytest ==                24 passed in 0.05s

Name                                    Stmts   Miss Branch BrPart  Cover
src/myagent/__init__.py                     7      0      0      0   100%
src/myagent/observability/__init__.py       3      0      0      0   100%
src/myagent/observability/logging.py       90      0     36      0   100%
TOTAL                                     100      0     36      0   100%
== all checks passed ==
```

pre-commit（9 个钩子，含 `mypy` local hook）在真实文件上全部 `Passed`。

环境问题与解决：本机 python.org 的 Python 3.12 框架版没有自带 CA 证书包，`pip install` 报
`CERTIFICATE_VERIFY_FAILED`；用 `SSL_CERT_FILE=/etc/ssl/cert.pem` 指向 macOS 系统信任库后安装成功，
该处理已固化进 `scripts/bootstrap.sh`。

## 8. 结论与遗留问题

结论：

1. 0.2 完成：仓库成为独立工程（pyproject + src layout + tests + docs + scripts），运行时不依赖任何第三方包。
2. 0.3 完成：代码规范（ruff）、类型标注（mypy strict）、pytest、基础 Logging、Commit 规范全部落地并**由一条命令验证**。
3. 验收标准的两道问题已在 `docs/architecture.md` 中回答，并给出可复现命令。

遗留问题（进入后续阶段）：

| 遗留项 | 计划 |
| --- | --- |
| 尚未 commit：`PLAN.md`、`.gitignore`、`docs/`、`src/`、`tests/`、`scripts/`，以及工作区里 `AGENTS.md` / `todo.md` / `docs_for_nano/…` 的删除 | 按「一个 Task 一次提交」补提交 |
| 仓库里残留空的 `my-agent-framework/` 目录（未纳入 git） | 确认后删除（ADR-0001 已定根目录方案） |
| 覆盖率下限、CI | Phase 9 设定（对齐上游 75%）并接入工作流 |
| LICENSE 未定 | Phase 9/10 定稿打包时补 |
