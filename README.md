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
| Phase 2 | 核心代码迁移：Model / Tool / Runner / Loop 抽象 | ✅ 已完成 |
| Phase 3 | Agent Framework 重构：模块职责与接口 | ✅ 已完成 |
| Phase 4 | Memory 系统改造：Working / Episodic / Semantic + 检索 | ✅ 已完成 |
| Phase 5 | RAG 系统建设：Loader → Chunker → Embedding → Store → Retriever | ✅ 已完成 |
| Phase 6 | Context Manager 重构：优先级与预算 | ✅ 已完成 |
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

# 4. 对话（.env 里配好 LLM_BASE_URL / LLM_MODEL / LLM_API_KEY 后）
myagent chat -m "现在几点？顺便用 calculator 算一下 (12+8)*3"
myagent chat                 # 交互模式：/exit 退出、/session 看会话 key、/clear 清空历史
myagent tools                # 列出已注册工具（离线可用，不需要密钥）
myagent memory list          # 看长期记忆（Phase 4；需要 Qdrant，见下方说明）
myagent memory search "我的研究方向"

# 5. 知识库（Phase 5）：摄取 → 检索 → 带引用的答案
myagent ingest docs/*.md                    # 摄取（幂等；首次自动探测 EMBED_DIM 并写回 .env）
myagent search "切分参数怎么定" -k 5         # 检索，输出 [文档id#块号] 引用
myagent docs list                           # 知识库里有哪些文档
myagent docs delete <document_id>           # 删一篇（向量 + 行一起删）

# 6. 上下文预算与压缩（Phase 6）
myagent chat --show-context -m "现在几点？"   # 打印七个 section 的 budget/used/dropped，再给答案
myagent session compact cli:default          # 把旧轮换成一个摘要检查点（--keep-recent 控制保留几轮）
```

Phase 3 之后的 Framework V2 可以独立运行，且**模块可替换、依赖可注入**：契约用 `Protocol`
定义、装配集中在 `myagent.runtime.build_agent()` 一处。把只读参照 `nanobot/` 删掉，上面的命令
照常工作（真实运行记录见 [`docs/records/phase-3-refactor.md`](./docs/records/phase-3-refactor.md)，
Phase 2 的独立运行证据在 [`docs/records/phase-2-migration.md`](./docs/records/phase-2-migration.md)）。
对话会以 JSONL 追加写入 `data/sessions/`（已 git 忽略），工具读写的沙箱目录默认是 `workspace/`
（其中的 `project-notes.md` 是给 `read_file` / `search_local` 用的示例语料）。

Phase 4 的**分层记忆**已接进 Loop：记录存 SQLite（`data/myagent.db`），向量存 Qdrant 的独立
collection（`myagent_memories`），`myagent chat` 每轮自动召回 + 自动写入（有策略，不是"全都记"）。
Qdrant 连不上时不会中断对话：召回降级为关键词搜索并给出提示。关闭开关用
`MYAGENT_MEMORY_ENABLED=false`。细节见 [`docs/memory-design.md`](./docs/memory-design.md)
与实验表 [`docs/records/phase-4-memory.md`](./docs/records/phase-4-memory.md)。

Phase 5 的 **RAG 流水线**是一条独立于对话的命令行能力（上游 nanobot 没有这一块）：
文件经 Loader → Chunker → Embedder 进入 Qdrant 的 `myagent_documents` collection，
原文与块号存 SQLite；`myagent search` 检索后给每个块附上 `[文档id#块号]` 引用，
`RagPipeline.build_context()` 把命中块拼成模型可以直接引用的上下文。
默认切分 800 字符 / 重叠 120 字符（400/800/1200 的实验见 ADR-0009）；
`EMBED_DIM` 留空时首次 ingest 会用一次真实调用探测维度并写回 `.env`。
细节见 [`docs/rag-design.md`](./docs/rag-design.md)
与实验表 [`docs/records/phase-5-rag.md`](./docs/records/phase-5-rag.md)。

Phase 6 的 **Context Manager** 把记忆与文档正式接进 prompt，并把「装不下怎么办」写成固定的
四步：按 section 配额裁剪 → 删孤儿 tool 结果 → 补缺失的 tool 结果 → 校验（实在装不下才报错）。
预算公式 `input_budget = context_window - max_output_tokens - 1024`，各来源配额 35/35/20/10
（ADR-0010）；每次 build 产出一份 `ContextReport`，`myagent chat --show-context` 直接打印它。
长会话用 `myagent session compact <key>` 压成一个摘要检查点，**原文仍留在 JSONL 里**。
细节见 [`docs/context-design.md`](./docs/context-design.md)
与实验表 [`docs/records/phase-6-context.md`](./docs/records/phase-6-context.md)。

> 如果 `myagent` 报 `ModuleNotFoundError: No module named 'myagent'`：本机 `.venv` 里的 `.pth`
> 被 macOS 打上了 `hidden` 标志，Python 的 `site` 会读不到它。执行 `chflags -R nohidden .venv`
> 即可（诊断细节见 [`docs/records/phase-3-refactor.md`](./docs/records/phase-3-refactor.md) §8.2）。

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
│   ├── runtime.py              # build_agent() / build_memory()：唯一装配点
│   ├── agent/                  # types / runner / loop（4 阶段）/ context（section + 预算）/ compaction（摘要检查点）/ token_budget（估算）/ runtime（运行参数）
│   ├── models/                 # BaseModel 协议 + OpenAI 兼容实现
│   ├── tools/                  # BaseTool 协议 / schema 校验 / Registry / builtin 四个工具
│   ├── session/                # SessionStore 契约 + JSONL 实现（追加式 + last_archived）
│   ├── memory/                 # 分层记忆：types / sqlite_store / vector_index / extractor / retriever / consolidator
│   ├── rag/                    # 端到端 RAG：loader / chunker / embedder / vectorstore / store / retriever / reranker / pipeline
│   ├── config/                 # .env 加载、Settings 单入口（LLM / Agent / SQLite / Qdrant / Embedding / Memory / RAG）
│   ├── observability/          # 日志等可观测性基础件
│   ├── tokens.py               # 全框架共用的 token 估算
│   └── cli.py                  # myagent chat|tools|memory|ingest|search|docs（只调用 build_*）
├── tests/                      # pytest 测试（641 项，覆盖率 100%；tests/rag/ 全部离线）
├── workspace/                  # 工具的沙箱工作区（默认 AGENT_WORKSPACE）
├── docs/                       # 设计文档、ADR、阶段记录
│   ├── design.md               # Framework V2 设计：模块地图 / 契约 / 装配图 / 差异表 / 答辩
│   ├── architecture.md         # 架构总览（上游）：启动路径 / 消息流 / 模块地图
│   ├── agent-loop.md           # AgentLoop 与 AgentRunner 深潜
│   ├── tool-system.md          # Tool 契约 / Registry / 发现 / 执行
│   ├── context.md              # 上下文组装 / 预算 / 压缩
│   ├── memory.md               # Session vs Memory / 归档 / Dream
│   ├── memory-design.md        # 分层记忆：分层表 / 存储 / 写入策略 / 检索 / 巩固（Phase 4）
│   ├── rag-design.md           # RAG：加载 / 切分 / 嵌入 / 向量库 / 检索 / 重排 / 引用（Phase 5）
│   ├── context-design.md       # 上下文：优先级 / 预算 / 四步拟合 / 压缩 / 答辩（Phase 6）
│   ├── development.md          # 开发规范（代码 / 测试 / Git / 日志 / 文档）
│   ├── decision-records/       # 架构决策记录（ADR）
│   └── records/                # 阶段工作记录
├── scripts/                    # bootstrap.sh / check.sh / check_doc_anchors.py / memory_experiment.py / rag_experiment.py / context_experiment.py
├── .env / .env.example         # 本地密钥（忽略） / 键名模板（提交）
├── data/                       # 本地运行状态：会话 JSONL + 记忆 SQLite（git 忽略）
└── nanobot/                    # 上游只读参照，不参与构建（git ignored）
```

## 技术选型

| 层 | 选型 | 决策记录 |
| --- | --- | --- |
| 文档与元数据存储 | SQLite（`sqlite3`，单文件，可上 FTS5 做混合检索） | ADR-0003 |
| 向量存储 | Qdrant（HNSW + payload 过滤，本地 docker / 云端同一套配置）；文档与记忆各一个 collection | ADR-0003、ADR-0008 |
| PDF 文本层 | `pypdf`（只用于 `rag/loader.py` 的 `PdfLoader`；不做 OCR） | ADR-0009 |
| Embedding | 阿里云 DashScope（`qwen3.7-text-embedding-flash`，OpenAI 兼容模式） | ADR-0005 |
| 配置与密钥 | `.env` + `python-dotenv` 的 `load_dotenv()`，环境变量优先 | ADR-0004 |
| Agent Runtime | 自研（对照 nanobot 的 Loop / Runner 拆分重新抽象） | ADR-0001、ADR-0002 |
| 模型客户端 | `openai>=1.50`（AsyncOpenAI），只被 `models/openai_compat.py` 依赖 | ADR-0006 |
| CLI | 标准库 `argparse`（不引 typer / rich / click） | ADR-0006 |

## 文档索引

**上游源码理解（Phase 1 产出）**

- [`docs/architecture.md`](./docs/architecture.md)：nanobot 如何启动、一条消息经过哪些模块、四条关键边界与模块地图。
- [`docs/agent-loop.md`](./docs/agent-loop.md)：7 阶段流水线、Runner 主循环、迭代上限、中途注入、checkpoint。
- [`docs/tool-system.md`](./docs/tool-system.md)：Tool 契约与 Schema、Registry 校验网关、自动发现、并发执行与错误语义。
- [`docs/context.md`](./docs/context.md)：system prompt 分层、预算公式、四步拟合、摘要压缩与空闲压缩。
- [`docs/memory.md`](./docs/memory.md)：Session 与 Memory 的边界、history.jsonl、摘要检查点、Dream 整合。

**Framework 设计与实现（Phase 2 / Phase 3 产出）**

- [`docs/design.md`](./docs/design.md)：Framework V2 的模块地图、九个契约的签名、装配图、与上游的「保留 / 简化 / 加法」差异表，以及「为什么 Loop 不负责 RAG」等四个答辩问题。
- [`docs/decision-records/0007-framework-extension-points.md`](./docs/decision-records/0007-framework-extension-points.md)：为什么扩展点用 `Protocol` + 装配注入，而不是 `isinstance` 分支或抽象基类。
- [`docs/decision-records/0006-phase2-dependencies.md`](./docs/decision-records/0006-phase2-dependencies.md)：为什么引入 `openai`、为什么 CLI 用 `argparse`，以及备选方案。
- [`docs/records/phase-2-migration.md`](./docs/records/phase-2-migration.md)：Phase 2 工作记录（含「删掉 nanobot 后」的真实 transcript 与落盘结构）。
- [`docs/records/phase-3-refactor.md`](./docs/records/phase-3-refactor.md)：Phase 3 工作记录（契约边界的验证方式、真实 transcript、质量门）。

**Memory（Phase 4 产出）**

- [`docs/memory-design.md`](./docs/memory-design.md)：四层记忆的分层表、`MemoryRecord` 字段、SQLite 表与 Qdrant payload、「写 / 不写」清单、检索与巩固、与上游 Dream 的对照表。
- [`docs/decision-records/0008-layered-memory.md`](./docs/decision-records/0008-layered-memory.md)：为什么记忆单独一个 collection、为什么「LLM 提议 + 代码裁决」、为什么衰减只给 Episodic、巩固游标为什么只有成功才前移。
- [`docs/records/phase-4-memory.md`](./docs/records/phase-4-memory.md)：Phase 4 工作记录（五张实验表、跨 Session 验收、质量门，以及 Qdrant id 归一化等四个真实问题）。

**RAG（Phase 5 产出）**

- [`docs/rag-design.md`](./docs/rag-design.md)：两段流水线（摄取 / 检索）、四类数据类型、SQLite 表与 Qdrant payload、维度探测、引用格式、与 Phase 4 记忆的边界、已知限制。
- [`docs/decision-records/0009-chunking-and-retrieval.md`](./docs/decision-records/0009-chunking-and-retrieval.md)：为什么默认 800/120、为什么 `top_k=5`、为什么本阶段不引入模型型 reranker、为什么 `pypdf` 成为运行依赖。
- [`docs/records/phase-5-rag.md`](./docs/records/phase-5-rag.md)：Phase 5 工作记录（chunk size 实验、reranker 对比、RAG OFF/ON、真实服务冒烟与质量门）。
- [`scripts/rag_experiment.py`](./scripts/rag_experiment.py)：跑出上面三张表的实验脚本（`--offline` 只跑关键词行）。

**Context（Phase 6 产出）**

- [`docs/context-design.md`](./docs/context-design.md)：七段结构、优先级表、四步拟合（裁剪 → 修结构 → 校验）、预算公式与四档配额、压缩的三条触发路径、与上游 `ContextBuilder` / `ContextGovernor` 的对照，以及「Context 太长怎么办」等三个答辩问题。
- [`docs/decision-records/0010-context-budget.md`](./docs/decision-records/0010-context-budget.md)：为什么用 `context_window - max_output_tokens - 1024`、为什么四档是 35/35/20/10、`None` 为什么表示「不检查」、什么条件下该改这些数字。
- [`docs/records/phase-6-context.md`](./docs/records/phase-6-context.md)：Phase 6 工作记录（预算裁剪表、压缩前后对比与探针、开关 ON/OFF、真实服务 transcript 与质量门）。
- [`scripts/context_experiment.py`](./scripts/context_experiment.py)：跑出上面三张表的实验脚本（`--offline` 跳过需要模型的压缩段）。

> 文档里的 `file.py:行号` 均可用 `.venv/bin/python scripts/check_doc_anchors.py` 校验
> （覆盖 `docs/`、`README.md` 与 `PLAN.md`，当前 1195 个锚点全部解析通过），避免文档与源码脱节。

**工程与决策**

- [`docs/development.md`](./docs/development.md)：Python / 测试 / Git / Logging / 文档规范。
- [`docs/decision-records/0001-project-layout-and-tooling.md`](./docs/decision-records/0001-project-layout-and-tooling.md)：目录布局与工具链选型。
- [`docs/decision-records/0002-nanobot-as-read-only-reference.md`](./docs/decision-records/0002-nanobot-as-read-only-reference.md)：为什么把 nanobot 当作只读参照而不是 fork。
- [`docs/decision-records/0003-storage-and-vector-store.md`](./docs/decision-records/0003-storage-and-vector-store.md)：SQLite + Qdrant 的存储选型。
- [`docs/decision-records/0004-secrets-and-env-files.md`](./docs/decision-records/0004-secrets-and-env-files.md)：密钥与环境变量的唯一读取路径。
- [`docs/decision-records/0005-embedding-provider.md`](./docs/decision-records/0005-embedding-provider.md)：Embedding 提供方与模型。
- [`docs/decision-records/0008-layered-memory.md`](./docs/decision-records/0008-layered-memory.md)：分层记忆的存储、写入策略与巩固游标。
- [`docs/records/`](./docs/records)：每个阶段的工作记录（问题 → Baseline → 方案 → 实现 → 实验 → 结果 → 结论）。

## 上游致谢

- [HKUDS/nanobot](https://github.com/HKUDS/nanobot)（v0.3.5，本地参照 commit `2fb165939`）：Agent Loop / Runner / Bus / Session / Memory 的核心设计来源。
