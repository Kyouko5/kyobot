# MyAgent

> 面向知识密集型任务的轻量级 Agent Framework —— 在读懂 nanobot 核心运行机制的基础上，
> 重新抽象 Agent Loop、Context、Memory、RAG 与 Tool 系统，并用一个垂直领域 Agent 验证它。

本仓库不是 nanobot 的 fork。上游源码以**只读参照**的方式放在 `nanobot/`（已被
`.gitignore` 排除），采用「理解 → 迁移 → 重新抽象 → 功能增强 → 测试」的路径重建自己的
代码结构；每一个关键抽象都对应一条决策记录（ADR）。

## 当前进度

| 阶段 | 目标 | 状态 |
| --- | --- | --- |
| Phase 0 | 项目准备：环境、仓库、开发规范、nanobot 跑通 | ✅ 已完成 |
| Phase 1 | nanobot 源码理解（Agent Runtime / Memory / Tool / Session） | ⬜ 未开始 |
| Phase 2 | 核心代码迁移：Model / Tool / Runner / Loop 抽象 | ⬜ 未开始 |
| Phase 3 | Agent Framework 重构：模块职责与接口 | ⬜ 未开始 |
| Phase 4 | Memory 系统改造：Working / Episodic / Semantic + 检索 | ⬜ 未开始 |
| Phase 5 | RAG 系统建设：Loader → Chunker → Embedding → Store → Retriever | ⬜ 未开始 |
| Phase 6 | Context Manager 重构：优先级与预算 | ⬜ 未开始 |
| Phase 7 | 垂直领域 Agent | ⬜ 未开始 |
| Phase 8 | Evaluation Pipeline | ⬜ 未开始 |
| Phase 9 | 工程化：测试、Logging、Docker | ⬜ 未开始 |
| Phase 10 | README / Demo / 简历包装 | ⬜ 未开始 |

完整路线与验收标准见 [`PLAN.md`](./PLAN.md)。

## 快速开始

```bash
# 1. 创建虚拟环境并安装开发依赖（本机 Python 缺少 CA 证书包，脚本会处理）
scripts/bootstrap.sh
source .venv/bin/activate

# 2. 质量门：ruff format --check / ruff check / mypy / pytest + coverage
scripts/check.sh
```

没有安装依赖时也可以直接跑测试（`pyproject.toml` 里配置了 `pythonpath = ["src"]`）：

```bash
PYTHONPATH=src python3 -m pytest
```

## 目录结构

```text
kyobot/
├── PLAN.md                     # 项目计划与验收标准（唯一路线入口）
├── pyproject.toml              # 打包、ruff、mypy、pytest、coverage 配置
├── src/myagent/                # Framework 本体
│   └── observability/          # 日志等可观测性基础件
├── tests/                      # pytest 测试
├── docs/                       # 设计文档、ADR、阶段记录
│   ├── architecture.md         # nanobot Baseline 架构与消息流（Phase 0 验收材料）
│   ├── development.md          # 开发规范（代码 / 测试 / Git / 日志 / 文档）
│   ├── decision-records/       # 架构决策记录（ADR）
│   └── records/                # 阶段工作记录
├── scripts/                    # bootstrap.sh / check.sh
└── nanobot/                    # 上游只读参照，不参与构建（git ignored）
```

## 文档索引

- [`docs/architecture.md`](./docs/architecture.md)：nanobot 如何启动、一条用户消息经过哪些模块，以及本项目的迁移计划。
- [`docs/development.md`](./docs/development.md)：Python / 测试 / Git / Logging / 文档规范。
- [`docs/decision-records/0001-project-layout-and-tooling.md`](./docs/decision-records/0001-project-layout-and-tooling.md)：目录布局与工具链选型。
- [`docs/decision-records/0002-nanobot-as-read-only-reference.md`](./docs/decision-records/0002-nanobot-as-read-only-reference.md)：为什么把 nanobot 当作只读参照而不是 fork。
- [`docs/records/`](./docs/records)：每个阶段的工作记录（问题 → Baseline → 方案 → 实现 → 实验 → 结果 → 结论）。

## 上游致谢

- [HKUDS/nanobot](https://github.com/HKUDS/nanobot)（v0.3.5，本地参照 commit `2fb165939`）：Agent Loop / Runner / Bus / Session / Memory 的核心设计来源。
