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
| Phase 1 | nanobot 源码理解（Agent Runtime / Memory / Tool / Session） | ✅ 已完成 |
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
# 1. 配置密钥（.env 已被 git 忽略；模板列出了全部需要的 key）
cp .env.example .env

# 2. 创建虚拟环境并安装依赖（本机 Python 缺少 CA 证书包，脚本会处理）
scripts/bootstrap.sh
source .venv/bin/activate

# 3. 质量门：ruff format --check / ruff check / mypy / pytest + coverage
scripts/check.sh
```

密钥统一通过 `python-dotenv` 的 `load_dotenv()` 读取（`MYAGENT_ENV_FILE` 可指向别处的 `.env`）：

```python
from myagent.config import require_env

api_key = require_env("LLM_API_KEY")  # 未填则抛 MissingEnvError，不会静默失败
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
│   ├── config/                 # .env 加载、类型化设置（SQLite / Qdrant）
│   └── observability/          # 日志等可观测性基础件
├── tests/                      # pytest 测试
├── docs/                       # 设计文档、ADR、阶段记录
│   ├── architecture.md         # 架构总览：启动路径 / 消息流 / 模块地图
│   ├── agent-loop.md           # AgentLoop 与 AgentRunner 深潜
│   ├── tool-system.md          # Tool 契约 / Registry / 发现 / 执行
│   ├── context.md              # 上下文组装 / 预算 / 压缩
│   ├── memory.md               # Session vs Memory / 归档 / Dream
│   ├── development.md          # 开发规范（代码 / 测试 / Git / 日志 / 文档）
│   ├── decision-records/       # 架构决策记录（ADR）
│   └── records/                # 阶段工作记录
├── scripts/                    # bootstrap.sh / check.sh / check_doc_anchors.py
├── .env / .env.example         # 本地密钥（忽略） / 键名模板（提交）
└── nanobot/                    # 上游只读参照，不参与构建（git ignored）
```

## 技术选型

| 层 | 选型 | 决策记录 |
| --- | --- | --- |
| 文档与元数据存储 | SQLite（`sqlite3`，单文件，可上 FTS5 做混合检索） | ADR-0003 |
| 向量存储 | Qdrant（HNSW + payload 过滤，本地 docker / 云端同一套配置） | ADR-0003 |
| Embedding | 阿里云 DashScope（`qwen3.7-text-embedding-flash`，OpenAI 兼容模式） | ADR-0005 |
| 配置与密钥 | `.env` + `python-dotenv` 的 `load_dotenv()`，环境变量优先 | ADR-0004 |
| Agent Runtime | 自研（对照 nanobot 的 Loop / Runner 拆分重新抽象） | ADR-0001、ADR-0002 |

## 文档索引

**上游源码理解（Phase 1 产出）**

- [`docs/architecture.md`](./docs/architecture.md)：nanobot 如何启动、一条消息经过哪些模块、四条关键边界与模块地图。
- [`docs/agent-loop.md`](./docs/agent-loop.md)：7 阶段流水线、Runner 主循环、迭代上限、中途注入、checkpoint。
- [`docs/tool-system.md`](./docs/tool-system.md)：Tool 契约与 Schema、Registry 校验网关、自动发现、并发执行与错误语义。
- [`docs/context.md`](./docs/context.md)：system prompt 分层、预算公式、四步拟合、摘要压缩与空闲压缩。
- [`docs/memory.md`](./docs/memory.md)：Session 与 Memory 的边界、history.jsonl、摘要检查点、Dream 整合。

> 文档里的 `file.py:行号` 均可用 `.venv/bin/python scripts/check_doc_anchors.py` 校验
> （覆盖 `docs/`、`README.md` 与 `PLAN.md`，当前 326 个锚点全部解析通过），避免文档与上游源码脱节。

**工程与决策**

- [`docs/development.md`](./docs/development.md)：Python / 测试 / Git / Logging / 文档规范。
- [`docs/decision-records/0001-project-layout-and-tooling.md`](./docs/decision-records/0001-project-layout-and-tooling.md)：目录布局与工具链选型。
- [`docs/decision-records/0002-nanobot-as-read-only-reference.md`](./docs/decision-records/0002-nanobot-as-read-only-reference.md)：为什么把 nanobot 当作只读参照而不是 fork。
- [`docs/decision-records/0003-storage-and-vector-store.md`](./docs/decision-records/0003-storage-and-vector-store.md)：SQLite + Qdrant 的存储选型。
- [`docs/decision-records/0004-secrets-and-env-files.md`](./docs/decision-records/0004-secrets-and-env-files.md)：密钥与环境变量的唯一读取路径。
- [`docs/decision-records/0005-embedding-provider.md`](./docs/decision-records/0005-embedding-provider.md)：Embedding 提供方与模型。
- [`docs/records/`](./docs/records)：每个阶段的工作记录（问题 → Baseline → 方案 → 实现 → 实验 → 结果 → 结论）。

## 上游致谢

- [HKUDS/nanobot](https://github.com/HKUDS/nanobot)（v0.3.5，本地参照 commit `2fb165939`）：Agent Loop / Runner / Bus / Session / Memory 的核心设计来源。
