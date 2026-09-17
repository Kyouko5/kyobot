# 项目总计划 Todo — nanobot 二次开发与 Data Analyst Agent

> 唯一总计划入口。内容提取自 [`docs_for_nano/nanobot_agent_project_plan.md`](./docs_for_nano/nanobot_agent_project_plan.md)，该计划书为需求与设计的最终依据。
> 项目周期：10~12 周 · Phase 0 ~ Phase 9
> 最后更新：2026-09-17

## 使用方式（工作流约定）

1. `todo.md` 是唯一的进度入口：每个阶段只在这里维护一份任务清单，不另建分散的 todo。
2. 阶段开始时把状态改为 🟡 进行中；阶段内每完成一个 Task，立即勾选对应复选框，不等阶段结束。
3. **每完成一个阶段，必须做两件事**：
   - 更新本文件的「进度总览」表、阶段状态、复选框与「变更日志」；
   - 输出一份该阶段的工作记录文档：`docs_for_nano/records/phase-<N>-<slug>.md`（见下方模板）。
4. 记录文档必须遵循计划书第 21 节「证据」原则，按 `问题 → Baseline → 方案 → 实现 → 实验 → 结果 → 结论` 组织；没有真实实验数据的结论不得写入。
5. 记录文档写完后，在本文件对应阶段下补一行 `记录：docs_for_nano/records/phase-N-xxx.md`。
6. Commit 节奏遵循计划书第 20 节，每个 Task 至少一次独立提交，禁止阶段末一次性大批量提交。
7. 未达验收标准的阶段不得标记为 ✅；未通过验收的条目要显式写成遗留问题并进入下一阶段风险清单。

## 进度总览

| 阶段 | 周期 | 核心目标 | 状态 | 记录文档 |
|---|---|---:|---|---|
| Phase 0 | 0.5 周 | 项目初始化与 Baseline | 🟡 进行中 | 待产出 |
| Phase 1 | 1~2 周 | 吃透 nanobot 架构 | ⬜ 未开始 | 待产出 |
| Phase 2 | 2 周 | 建立 Memory / Agent Baseline | ⬜ 未开始 | 待产出 |
| Phase 3 | 2 周 | Memory Engine 重构 | ⬜ 未开始 | 待产出 |
| Phase 4 | 1.5~2 周 | Retrieval / RAG Engine | ⬜ 未开始 | 待产出 |
| Phase 5 | 1 周 | Context Manager 重构 | ⬜ 未开始 | 待产出 |
| Phase 6 | 1 周 | Tool Layer 与数据工具 | ⬜ 未开始 | 待产出 |
| Phase 7 | 1~1.5 周 | Evaluation 与 Observability | ⬜ 未开始 | 待产出 |
| Phase 8 | 1.5~2 周 | Data Analyst Agent | ⬜ 未开始 | 待产出 |
| Phase 9 | 0.5~1 周 | 工程化与项目包装 | ⬜ 未开始 | 待产出 |

状态图例：⬜ 未开始 · 🟡 进行中 · ✅ 已完成 · ⛔ 阻塞

## 阶段记录文档模板

```markdown
# Phase N 工作记录：<阶段名>

- 日期：YYYY-MM-DD ~ YYYY-MM-DD
- 状态：已完成 / 部分完成（遗留问题见末尾）
- 关联提交：<commit hash> <message>

## 1. 阶段目标
## 2. 问题定义
（本阶段要解决的具体问题，尽量可度量）

## 3. Baseline
（改造前的行为与指标，注明测量方式与数据规模）

## 4. 方案设计
（架构图 / 接口定义 / 关键取舍与备选方案）

## 5. 实现
（改动文件清单与职责说明，指向具体路径）

## 6. 实验设置
（数据集、样本量、指标、运行命令）

## 7. 结果
（Baseline vs 新方案 的真实数据表格）

## 8. 结论与遗留问题
（哪些问题解决了，哪些没有；下一阶段风险）
```

---

## Phase 0：项目初始化与 Baseline（0.5 周）

**目标**：建立开发环境、代码基线和最小可运行版本，后续所有改造都建立在可比较的 Baseline 之上。

### 任务

- [x] **Task 0.1 Fork / Clone nanobot**
  - [x] 建立自己的 Git 仓库（根仓库 `Kyouko5/kyobot`，分支 `main`）
  - [x] 保留 upstream 信息（`nanobot/` 的 `origin` → `HKUDS/nanobot`，可单独同步）
  - [ ] 建立 `dev` 分支（当前仅有 `main`）
  - [x] 记录当前 nanobot commit / version：`2fb16593988b9e85131e02f395bb9a5108e220e7`（v0.3.5），记录日期 2026-09-17
- [ ] **Task 0.2 完成本地环境**：Python、LLM Provider、Embedding Model、Reranker（后续阶段）、SQLite、PostgreSQL / MySQL（至少一个）、Pandas、基础测试环境
  - [x] Python 环境（本机 3.12.5，`nanobot/.venv` 已创建）
  - [ ] LLM Provider 可用（API Key 配置并连通）
  - [ ] Embedding Model 可用
  - [ ] 至少一个关系型数据库可用（SQLite + PostgreSQL / MySQL）
  - [ ] Pandas 与测试依赖可用（`pytest` 可跑通）
- [ ] **Task 0.3 跑通原始 Demo**：普通 Chat、多轮对话、Tool Calling、Memory、Session、Agent Loop
- [ ] **Task 0.4 初始化项目目录**：`my-agent-framework/` 下建立 `framework/{agent,context,memory,retrieval,tools,evaluation,observability}/`、`applications/data-analyst-agent/`、`benchmarks/`、`datasets/`、`docs/`、`scripts/`、`tests/`（当前该目录为空）

### 产出

`README.md`、`docs/architecture.md`、环境配置文档、可运行的 nanobot Baseline。

### 验收标准

- [ ] Agent 可以正常启动
- [ ] 可以完成基础 Tool Calling
- [ ] 可以完成多轮对话
- [ ] Memory 可以工作
- [ ] 基础测试可以运行
- [ ] Baseline commit 已固定

---

## Phase 1：深入理解 nanobot 架构（1~2 周）

**目标**：不急于增加新功能，先彻底理解运行流程、数据流与扩展点。

重点模块：`AgentLoop`、`AgentRunner`、`ContextBuilder`、`Memory`、`Session`、`Provider`、`Tools`、`MCP`、`Channels`、`Config`、`Skills`。

### 任务

- [ ] **Task 1.1 画出 Runtime 调用链**：User → Channel → MessageBus → AgentLoop → AgentRunner → Provider → Tool Calling → AgentRunner → AgentLoop → Response
- [ ] **Task 1.2 分析 Context 构建**：System Prompt + Memory + Session History + Skills + Tool State + User Query 如何组装成最终上下文
- [ ] **Task 1.3 分析 Memory 生命周期**：Conversation → Extraction → Storage → Retrieval → Context Injection
- [ ] **Task 1.4 分析 Tool 生命周期**：Query → Tool Selection → Validation → Execution → Tool Result → LLM，并评估 SQL / Python / File / Schema Tool 的接入方式
- [ ] **Task 1.5 寻找 Extension Point**：明确保留哪些核心模块、哪些需要 Interface、哪些应依赖抽象、哪些逻辑属于 Framework 或 Application

### 产出

`docs/architecture.md`、`agent-runtime.md`、`context-flow.md`、`memory-current.md`、`tool-system.md`、`extension-points.md`

### 验收标准

能脱离源码回答：消息如何进入 Agent、Agent 如何选择与执行 Tool、Memory 如何影响下一轮 Context、如何新增一个 Tool、哪些模块可替换、为什么主要改造点选在 Memory / Retrieval / Context / Tool。

---

## Phase 2：建立 Memory 与 Agent Baseline（2 周）

**目标**：先用实际数据证明原有方案在长期记忆、跨 Session 与数据分析任务上的问题，再动架构。

### 任务

- [ ] **Task 2.1 构建 Memory Benchmark**：`benchmarks/memory/` 下覆盖 `single_fact`、`multi_turn`、`cross_session`、`stale_memory`、`conflicting_memory`、`user_preference`、`procedural_memory`
- [ ] **Task 2.2 构建 Data Analysis Task Benchmark**：`benchmarks/agent/` 下覆盖 `simple_qa`、`aggregation`、`filtering`、`multi_table_join`、`trend_analysis`、`anomaly_detection`、`cohort_analysis`、`multi_step_analysis`
- [ ] **Task 2.3 定义 Memory 指标**：Memory Recall、Memory Precision、Cross-session Recall、Stale Memory Rate、Conflict Resolution Rate
- [ ] **Task 2.4 定义 Agent 指标**：Task Success Rate、SQL Success Rate、Tool Call Success Rate、Answer Correctness、Average Steps、Latency、Token Usage
- [ ] **Task 2.5 跑出 Baseline 报告**：在原始 nanobot 上跑全部数据集并归档失败案例

### 产出

`benchmarks/memory_dataset.jsonl`、`agent_dataset.jsonl`、`evaluation_config.yaml`、`baseline_report.md`

### 验收标准

形成 `Baseline → Dataset → Metrics → Failure Cases → Problem Definition` 闭环，并能明确回答：原始 nanobot 在数据分析场景下最值得改造的问题是什么？

---

## Phase 3：Memory Engine 重构（2 周）

**目标**：把 Memory 升级为独立、可插拔、可评测的 Memory Engine。

### 任务

- [ ] **Task 3.1 定义 Memory Model**：`id / content / type / scope / source / confidence / importance / created_at / updated_at / status / metadata`
- [ ] **Task 3.2 实现 Memory 类型**：Episodic（历史事件）、Semantic（业务定义与数据事实）、Procedural（分析流程偏好）
- [ ] **Task 3.3 抽象 MemoryStore**：接口 `add / get / search / update / delete`，至少实现 `FileMemoryStore`、`SQLiteMemoryStore`，有余力再补 `VectorMemoryStore`
- [ ] **Task 3.4 实现 Memory Retrieval**：Candidate Retrieval → Metadata Filter → Score（relevance / importance / freshness / scope / confidence）→ Rank → Top-K
- [ ] **Task 3.5 实现 Memory Consolidation**：Raw Conversation → Extraction → Classification → Deduplication → Conflict Resolution → Importance Scoring → Persist
- [ ] **Task 3.6 落地分析偏好与业务知识长期记忆**：用户常用指标、默认时间粒度、常用筛选条件与维度、业务术语定义、历史分析结论、常用数据源

### 产出

`framework/memory/{models,manager,store,retrieval,consolidation,ranking}.py` 与 `backends/{file,sqlite,vector}.py`（集成层由 `MemoryManager` 统一编排 Writer / Retriever / Consolidator / Store）

### 验收标准

- [ ] Memory Store 可替换
- [ ] Memory 类型可扩展
- [ ] 支持跨 Session 查询
- [ ] 支持 Memory 更新与淘汰
- [ ] 支持基本冲突解决
- [ ] 能保存用户分析偏好
- [ ] 能保存业务指标定义
- [ ] 原有 Memory 功能没有被破坏
- [ ] Memory Benchmark 有真实对比结果

---

## Phase 4：Retrieval / RAG Engine（1.5~2 周）

**目标**：把 Retrieval 从 Application 中抽离为独立 Engine，服务指标定义、业务规则、数据字典、字段说明、表关系、分析规范与历史报告。

### 任务

- [ ] **Task 4.1 数据文档 Ingestion**：支持 Markdown、PDF / 文本、Data Dictionary、CSV Metadata、Database Schema、业务文档、历史报告，统一为 Document → Chunk → Embedding → Index
- [ ] **Task 4.2 Chunking**：Fixed-size、Recursive、Markdown-aware、Table-aware，并保留 `document_id / chunk_id / source / table_name / column_name / section / metadata`
- [ ] **Task 4.3 Dense Retrieval**：Query → Embedding → Vector DB → Top-K
- [ ] **Task 4.4 Sparse Retrieval**：BM25 或 SQLite FTS，重点覆盖字段名、表名、指标名、SQL 函数与专业术语（如“复购率” → `repeat_customer_rate`）
- [ ] **Task 4.5 Hybrid Retrieval**：Dense + Sparse + RRF / Score Fusion
- [ ] **Task 4.6 Reranker**：Recall Top 20 → Rerank → Top 5

### 产出

`framework/retrieval/{base,dense,sparse,hybrid,reranker,fusion,chunking,ingestion}.py`

### 验收标准

完成 `Dense only / Sparse only / Hybrid / Hybrid + Rerank` 四组对比实验，并比较 Recall@K、MRR、nDCG、Latency。

---

## Phase 5：Context Manager 重构（1 周）

**目标**：解决“检索到了什么”不等于“最终应该给 LLM 什么”。

### 任务

- [ ] **Task 5.1 Context Budget**：为 System Prompt、Recent History、Memory、Business RAG、Schema、Tool Results 分配 Token 预算（示例总预算 10K）
- [ ] **Task 5.2 Context Priority**：System > Current Task > Current Schema > Recent Tool Results > Relevant Memory > Relevant RAG > Low-priority History
- [ ] **Task 5.3 Schema Context 压缩**：Database → Schema Retrieval → Relevant Tables → Relevant Columns → LLM，避免全量 Schema 注入
- [ ] **Task 5.4 Tool Result Context**：大结果集先 Summary / Sampling 再入上下文，避免数十万行直接进 Prompt
- [ ] **Task 5.5 去重与耗时观测**：Memory / RAG 去重，记录 Context 构建耗时与 Token 使用

### 产出

`framework/context/{manager,budget,ranking,compression,schema_context,models}.py`

### 验收标准

- [ ] 支持 Context Token Budget
- [ ] 支持 Schema 动态筛选
- [ ] 支持 Tool Result 压缩
- [ ] 支持 Memory / RAG 去重
- [ ] Context 超限不会直接导致异常
- [ ] 记录 Context 构建耗时和 Token 使用

---

## Phase 6：Tool Layer 与数据工具（1 周）

**目标**：增强 nanobot Tool 系统，使其支撑完整数据分析工作流。Tool 分层为 Data Tools（SQL / Schema / CSV）、Analysis Tools（Python / Chart）、Support Tools（Search / File / Docs）。

### 任务

- [ ] **Task 6.1 SQL Tool**：`connect / schema / describe_table / execute_query`，含参数校验、Query Timeout、只读模式、错误捕获、行数限制与基础安全控制
- [ ] **Task 6.2 Schema Tool**：`list_databases / list_tables / describe_table / foreign_keys / search_columns`，帮助 Agent 生成 SQL 前先理解结构
- [ ] **Task 6.3 Python Analysis Tool**：受控环境（Pandas / NumPy / Matplotlib），支持统计分析、聚合、趋势、异常检测、相关性与简单建模
- [ ] **Task 6.4 File Tool**：读取 CSV / XLSX / JSON，查看 Schema、采样数据、执行分析、生成结果
- [ ] **Task 6.5 Chart Tool**：line / bar / pie / histogram / scatter，流程为 Analysis → Chart Specification → Chart Tool → Image → Agent Explanation

### 产出

`framework/tools/{base,router,sql,schema,python,file,chart}.py`

### 验收标准

- [ ] SQL 查询
- [ ] Schema 查询
- [ ] CSV / Excel 读取
- [ ] Pandas 分析
- [ ] 基础图表生成
- [ ] Tool Error 能被 Agent 感知并恢复

---

## Phase 7：Evaluation 与 Observability（1~1.5 周）

**目标**：把系统从“能跑”提升到“可评测、可分析、可优化”。

### 任务

- [ ] **Task 7.1 统一 Trace**：记录 `request_id / session_id / query / memory_hits / rag_hits / schema_context / tool_calls / sql / python_execution / llm_calls / tokens / latency / final_answer`
- [ ] **Task 7.2 Memory Evaluation**：Memory Recall、Memory Precision、Cross-session Recall、Stale Memory Rate、Conflict Resolution Rate
- [ ] **Task 7.3 Retrieval Evaluation**：Recall@K、Precision@K、MRR、nDCG
- [ ] **Task 7.4 SQL Evaluation**：SQL Execution Success Rate、SQL Result Correctness、Schema Linking Accuracy
- [ ] **Task 7.5 Agent Evaluation**：Task Success Rate、Tool Call Success Rate、SQL Success Rate、Answer Correctness、Average Steps、Latency、Token Usage
- [ ] **Task 7.6 Ablation Study**：A 原始 nanobot / B +Memory Engine / C +Dense Retrieval / D +Hybrid Retrieval / E +Hybrid+Reranker / F +Context Manager / G +Improved Tool Layer

### 产出

`framework/evaluation/{evaluator,metrics,runner,traces,report}.py`，以及 `benchmarks/reports/{memory_baseline,retrieval_ablation,sql_agent_eval,end_to_end_eval}.md`

### 验收标准

能用真实实验回答“Framework 重构解决了什么问题”，并给出 `Baseline vs New Framework` 的指标变化；同时说明哪个模块有效、哪种任务受益最大、哪些场景仍存在问题、哪些改造带来额外延迟或 Token 成本。

---

## Phase 8：Data Analyst Agent（1.5~2 周）

**目标**：用重构后的 Framework 构建数据分析 Agent，而不是另写一套独立 Agent。

### 任务

- [ ] **Task 8.1 Workflow A 简单数据问答**：识别数据源 → Schema Retrieval → 生成 SQL → SQL Validation → 执行 → 结果解释 → 回答
- [ ] **Task 8.2 Workflow B 趋势分析**：Planning → Schema Retrieval → SQL → Execute → Python Analysis → Trend Detection → Chart → Narrative
- [ ] **Task 8.3 Workflow C 异常分析**：Retrieve Metrics Definition → Query Historical Data → Python Analysis → Detect Anomaly → Retrieve Business Context → Explain Causes → Report
- [ ] **Task 8.4 Workflow D 复杂多步骤分析**：Planner → Task Decomposition（找表 / 查数 / 聚合 / 对比 / 异常 / 业务定义 / 可视化）→ Intermediate Results → Validation → Final Report
- [ ] **Task 8.5 应用侧 Memory 接入**：用户偏好、业务知识、历史分析结论、Procedural Memory（如销售分析默认同比 → 环比 → 异常检测 → 地区拆分）
- [ ] **Task 8.6 应用侧 RAG 接入**：业务定义、数据字典、字段解释、表关系、分析规范、历史报告；RAG 只提供定义，真实计算交给 SQL + Python
- [ ] **Task 8.7 数据源渐进支持**：MVP 为 CSV / SQLite / PostgreSQL，V2 加 MySQL / Excel，V3 再考虑数仓 / BigQuery / Snowflake / ClickHouse
- [ ] **Task 8.8 完成 Application MVP 五项能力**：自然语言转 SQL、多表 Join、趋势 / 对比分析、异常检测、图表生成
- [ ] **Task 8.9 安全与工程约束**：SQL 只读 + timeout + 行数限制 + 语句校验 + 禁止 DROP/DELETE/UPDATE/INSERT/ALTER/TRUNCATE；Python 沙箱 + 超时 + 内存限制 + 包白名单 + 文件访问限制；Tool 统一输入校验、超时、错误处理与结果 Schema
- [ ] **Task 8.10 进阶能力（MVP 之后）**：Query Rewrite、SQL Self-Repair、Tool Failure Recovery、数据质量检查、结果交叉验证、历史分析对比、多轮追问、分析报告持久化

### 产出

`applications/data-analyst-agent/` 完整实现与工作流编排代码。

### 验收标准

- [ ] 五条 MVP 能力可端到端跑通
- [ ] Agent 复用 Framework 的 Memory / Retrieval / Context / Tool 组件，无重复实现
- [ ] 分析结果可追溯（引用用到的 Schema、SQL、指标定义）
- [ ] 失败任务能重试或给出可理解的失败原因

---

## Phase 9：工程化与项目包装（0.5~1 周）

**目标**：把项目整理成可交付、可展示、可复现的成果。

### 任务

- [ ] **Task 9.1 工程化补齐**：CI/CD、Unit Test、Integration Test、Docker、Docker Compose、Environment Config、Logging、Error Handling
- [ ] **Task 9.2 撰写 README**：Project Motivation / Architecture / Core Improvements / Data Analyst Agent / Benchmark / Demo / Quick Start / Evaluation / Engineering / Roadmap
- [ ] **Task 9.3 准备 Demo 场景**：CSV 分析、PostgreSQL 分析、多表 Join、趋势分析 + 图表、异常检测、多轮追问、Memory 记住分析偏好、RAG 理解业务指标定义
- [ ] **Task 9.4 整理简历与面试材料**：简历项目描述（指标必须来自真实实验）、面试 6 问主线（为什么二次开发 / 为什么改 Memory / 为什么 RAG 不直接塞 Prompt / 为什么需要 Memory / 为什么不止 Text-to-SQL / 如何证明 Framework 有价值）

### 产出

Docker 化部署、CI 配置、最终 README、Demo 录屏 / 截图、简历与面试材料。

### 验收标准

- [ ] 全新环境按 README 可一键启动
- [ ] CI 覆盖 lint / 测试 / 构建
- [ ] 8 个 Demo 场景全部可演示
- [ ] 所有对外指标均有 `benchmarks/reports/` 中的实验支撑

---

## 全局约束

### 证据原则（每个阶段都必须满足）

统一采用：`问题 → Baseline → 方案 → 实现 → 实验 → 结果 → 结论`。每完成一个技术改造就留下可验证的证据，结论必须由真实实验数据支撑，不得预先填入未经验证的指标。

### 安全边界

- SQL：只读、超时、行数限制、语句校验、禁用写操作与 DDL。
- Python：沙箱执行、超时、内存限制、包白名单、文件访问限制。
- Tool：统一的输入校验、超时、错误处理与结果 Schema。

### Commit 节奏

按计划书第 20 节建议推进（docs → test baseline → memory 接口与三类 Memory → retrieval → context → tools → evaluation → agent → 多步工作流 → benchmark/demo），每个 Task 独立提交，便于用 Git History 展示技术演进。

---

## 变更日志

| 日期 | 变更 |
|---|---|
| 2026-09-17 | 初始版本：从计划书提取 Phase 0 ~ Phase 9 全部任务、产出与验收标准；确立阶段记录文档规范与路径 |
