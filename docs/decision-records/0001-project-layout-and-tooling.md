# ADR 0001：目录布局与工具链选型

- 状态：已接受
- 日期：2026-09-17
- 关联：Phase 0（`.gitignore`、`pyproject.toml`、`scripts/check.sh`、`src/myagent/observability/logging.py`）

## 背景

计划书要求「建立自己的项目」与「建立开发规范」。可选项有三类：仓库根目录直接作为工程根、
在仓库里再套一层 `my-agent-framework/`、或者用 src layout / flat layout。

## 决策

1. `kyobot` 仓库根目录就是工程根：`src/`、`tests/`、`docs/`、`pyproject.toml` 全部平铺在根目录。
2. 采用 **src layout**（`src/myagent/`），构建后端用 hatchling。
3. 格式化与静态检查统一用 ruff（`ruff format` 兼容 black），类型检查用 `mypy --strict`，
   测试用 pytest（`asyncio_mode = "auto"`）。
4. 质量门收敛成一条命令：`scripts/check.sh`。
5. 运行时依赖在 Phase 0 保持为空，日志用标准库 `logging` 实现。

## 理由

- 计划书的 Phase 1 / Phase 4 / Phase 10 产出物（`docs/architecture.md`、`docs/rag-design.md`、
  最终目录树）都写在根目录层级；再套一层目录只会让路径和导入变长。
- src layout 让「测试跑的是已安装的包」而不是工作区里的同目录文件，能提前暴露打包问题
  （例如忘记把子包写进 wheel），这对一个最终要 `pip install` 的 Framework 项目是必要的。
- 用 ruff 而不是 black + flake8 + isort 三件套：一个工具、一次遍历、一套配置，减少维护面；
  `line-length = 100` 与上游 nanobot 对齐，迁移代码不会产生无意义的格式 diff。
- 先零依赖再按阶段加依赖，可以在进入 Phase 4/5（memory 与 RAG）时清楚地说明每个依赖解决什么问题，
  而不是一开始就堆一个 requirements。

## 后果

- 所有命令都假设仓库根目录为工作目录（`scripts/*.sh` 内部会 `cd` 到根）。
- `pythonpath = ["src"]` 与「可编辑安装」两条路都能跑测试；前者用于快速反馈，后者用于验证打包。
- mypy strict 意味着新增公共 API 时必须写类型标注，短期略慢、长期降低接口漂移。

## 备选方案

| 方案 | 未采用的原因 |
| --- | --- |
| 在仓库里套一层 `my-agent-framework/` | 计划书里的目录名指的是「独立仓库」，而本仓库已经是独立仓库；嵌套会让相对路径与文档链接变复杂 |
| flat layout（`myagent/` 放在根目录） | 源码目录、测试目录与 `nanobot/` 参照目录混在同一层，`pytest` 与工具配置更容易误扫 |
| black + flake8 + isort | 三个工具、三套配置，收益与 ruff 重叠 |
| Poetry / PDM 管理依赖 | 当前用标准 `venv + pip + pyproject` 已足够；引入额外包管理器只增加一个需要解释的依赖 |
