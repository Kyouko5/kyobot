# nanobot 二次开发与数据分析 Agent 项目计划

> 项目目标：基于开源 Agent Framework **nanobot** 进行二次开发与架构重构，重点升级 Memory、RAG、Context、Tool 与 Evaluation 能力，并基于重构后的框架落地一个 **Data Analyst Agent / 数据分析 Agent**。
>
> 项目定位：**Agent Framework 二次开发 + Agent Infra 能力建设 + 数据分析 Agent 应用落地**
>
> 推荐周期：**10~12 周**
>
> 最终成果：
>
> 1. 一个基于 nanobot 二次开发的、可扩展的 Agent Framework。
> 2. 一套可插拔的 Memory / Retrieval / Context / Tool 架构。
> 3. 一套完整的 Evaluation / Benchmark Pipeline。
> 4. 一个能够连接数据库、CSV/Excel 等数据源并自主完成分析任务的数据分析 Agent。
> 5. 完整的工程文档、Demo、实验结果与简历项目描述。

---

# 1. 项目总体目标

项目最终形成两层结构：

```text
                    ┌─────────────────────────────┐
                    │       Data Analyst Agent    │
                    │   数据分析 / BI / 数据问答   │
                    └──────────────┬──────────────┘
                                   │
                    ┌──────────────▼──────────────┐
                    │     Your Agent Framework     │
                    │       based on nanobot      │
                    ├─────────────────────────────┤
                    │ Agent Runtime                │
                    │ Context Manager              │
                    │ Memory Engine                │
                    │ Retrieval Engine              │
                    │ Tool Engine                  │
                    │ Evaluation / Observability   │
                    └──────────────┬──────────────┘
                                   │
             ┌─────────────────────┼─────────────────────┐
             ▼                     ▼                     ▼
            LLM              Data / Knowledge         Tools
                                  Sources
                          ┌────────┼─────────┐
                          ▼        ▼         ▼
                       SQL DB    Files      Docs
```

## 1.1 Framework 层目标

重点解决以下问题：

- 理解并梳理 nanobot 的 Agent Runtime、Context、Memory、Tools 等核心模块。
- 将 Memory 能力从具体实现中解耦，设计可插拔 Memory Interface。
- 将 Memory 分为 Episodic / Semantic / Procedural 等类型。
- 构建独立 Retrieval Engine，支持 Dense / Sparse / Hybrid Retrieval。
- 引入 Reranking、Fusion、Metadata Filtering 等能力。
- 增加 Context Manager，负责检索结果筛选、去重和 Token Budget 管理。
- 将 Memory Consolidation 从具体 Prompt / 文件操作提升为独立生命周期。
- 强化 Tool Abstraction，使数据库、Python、文件、Schema 等工具可以独立扩展。
- 建立 Evaluation Pipeline，用数据而不是主观感受验证改造效果。
- 保持 Agent Loop 稳定，避免为了“二次开发”而大面积重写原有 Runtime。

## 1.2 Application 层目标

基于重构后的 Framework 实现：

> **Data Analyst Agent / 数据分析 Agent**

核心能力：

- 自然语言数据问答。
- 数据库 Schema 理解。
- Text-to-SQL。
- SQL 执行与结果验证。
- Python / Pandas 数据分析。
- 数据清洗与简单特征处理。
- 自动生成统计分析与图表。
- 业务知识 / 指标定义 RAG。
- 用户分析偏好长期记忆。
- 多步骤数据分析 Agent Workflow。
- 分析结果解释与可追溯引用。
- 支持 CSV / Excel / PostgreSQL / MySQL 等数据源。
- 基础任务失败重试与结果校验。

---

# 2. 项目核心技术主线

整个项目建议始终围绕下面这条主线推进：

```text
研究 nanobot
      ↓
理解 Agent Runtime
      ↓
建立原始 Baseline
      ↓
发现 Memory / Retrieval / Context / Tool 扩展问题
      ↓
重构 Memory Engine
      ↓
构建 Retrieval Engine
      ↓
构建 Context Manager
      ↓
增强 Tool Abstraction
      ↓
建立 Evaluation Pipeline
      ↓
基于 Framework 构建 Data Analyst Agent
      ↓
通过真实数据分析任务验证 Framework
```

核心思想：

> **不是“在 nanobot 上增加一个数据分析 Demo”，而是“先建设一套可扩展 Agent Framework，再用数据分析 Agent 验证这套 Framework”。**

---

# 3. 阶段总览

| 阶段 | 时间 | 核心目标 | 关键产出 |
|---|---:|---|---|
| Phase 0 | 0.5 周 | 项目初始化与 Baseline | 项目仓库、环境、任务看板 |
| Phase 1 | 1~2 周 | 吃透 nanobot 架构 | 架构图、调用链、模块说明 |
| Phase 2 | 2 周 | 建立 Memory / Agent Baseline | Benchmark、问题清单 |
| Phase 3 | 2 周 | Memory Engine 重构 | MemoryManager、Store、Lifecycle |
| Phase 4 | 1.5~2 周 | 构建 Retrieval / RAG Engine | Hybrid Retrieval、Reranker |
| Phase 5 | 1 周 | Context Manager 重构 | Context Budget、Context Assembly |
| Phase 6 | 1 周 | Tool Layer 与数据工具 | SQL / Python / File / Schema Tools |
| Phase 7 | 1~1.5 周 | Evaluation 与 Observability | Benchmark、Trace、Ablation |
| Phase 8 | 1.5~2 周 | Data Analyst Agent | 数据分析 Agent MVP |
| Phase 9 | 0.5~1 周 | 工程化与项目包装 | Docker、README、Demo、简历材料 |

---

# 4. Phase 0：项目初始化与 Baseline

**周期：第 1 周前半**

## 4.1 阶段目标

建立开发环境、代码基线和最小可运行版本，后续所有改造都建立在可比较的 Baseline 之上。

## 4.2 阶段任务

### Task 0.1：Fork / Clone nanobot

- 建立自己的 Git 仓库。
- 保留 upstream 信息，方便同步。
- 建立 `main` / `dev` 分支。
- 记录当前 nanobot commit / version。

### Task 0.2：完成本地环境

准备：

```text
Python
LLM Provider
Embedding Model
Reranker（后续阶段）
SQLite
PostgreSQL / MySQL（至少一个）
Pandas
基础测试环境
```

### Task 0.3：跑通原始 Demo

至少验证：

- 普通 Chat。
- 多轮对话。
- Tool Calling。
- Memory。
- Session。
- Agent Loop。

### Task 0.4：初始化项目目录

```text
my-agent-framework/
├── framework/
│   ├── agent/
│   ├── context/
│   ├── memory/
│   ├── retrieval/
│   ├── tools/
│   ├── evaluation/
│   └── observability/
│
├── applications/
│   └── data-analyst-agent/
│
├── benchmarks/
├── datasets/
├── docs/
├── scripts/
└── tests/
```

## 4.3 阶段产出

- 可运行 nanobot Baseline。
- Git 仓库。
- `README.md`。
- `docs/architecture.md`。
- 环境配置文档。

## 4.4 验收标准

- [ ] Agent 可以正常启动。
- [ ] 可以完成基础 Tool Calling。
- [ ] 可以完成多轮对话。
- [ ] Memory 可以工作。
- [ ] 基础测试可以运行。
- [ ] Baseline commit 已固定。

---

# 5. Phase 1：深入理解 nanobot 架构

**周期：第 1~2 周**

## 5.1 阶段目标

不急于增加新功能，首先彻底理解 nanobot 的运行流程、数据流和扩展点。

## 5.2 重点模块

```text
AgentLoop
AgentRunner
ContextBuilder
Memory
Session
Provider
Tools
MCP
Channels
Config
Skills
```

## 5.3 阶段任务

### Task 1.1：画出 Runtime 调用链

能够解释：

```text
User
 ↓
Channel
 ↓
MessageBus
 ↓
AgentLoop
 ↓
AgentRunner
 ↓
Provider
 ↓
Tool Calling
 ↓
AgentRunner
 ↓
AgentLoop
 ↓
Response
```

### Task 1.2：分析 Context 构建

明确：

```text
System Prompt
+
Memory
+
Session History
+
Skills
+
Tool State
+
User Query
```

如何被组装成最终模型上下文。

### Task 1.3：分析 Memory 生命周期

```text
Conversation
 ↓
Memory Extraction
 ↓
Memory Storage
 ↓
Memory Retrieval
 ↓
Context Injection
```

### Task 1.4：分析 Tool 生命周期

```text
User Query
 ↓
LLM Tool Selection
 ↓
Tool Validation
 ↓
Tool Execution
 ↓
Tool Result
 ↓
LLM
```

重点关注后续如何接入：

```text
SQL Tool
Python Tool
File Tool
Schema Tool
```

### Task 1.5：寻找 Extension Point

明确：

- 保留哪些核心模块。
- 哪些模块需要 Interface。
- 哪些模块应该依赖抽象，而不是具体实现。
- 哪些逻辑应该留在 Framework。
- 哪些逻辑只能属于 Application。

## 5.4 文档要求

至少产出：

```text
docs/
├── architecture.md
├── agent-runtime.md
├── context-flow.md
├── memory-current.md
├── tool-system.md
└── extension-points.md
```

## 5.5 阶段验收标准

能够脱离源码回答：

1. 一条用户消息如何进入 Agent。
2. Agent 如何选择和执行 Tool。
3. Memory 如何影响下一轮 Context。
4. 如何加入一个新的 Tool。
5. 哪些模块可以被替换。
6. 为什么主要改造点选择 Memory / Retrieval / Context / Tool。

---

# 6. Phase 2：建立 Memory 与 Agent Baseline

**周期：第 3~4 周**

## 6.1 阶段目标

先用实际数据证明原有方案在长期记忆、跨 Session 和数据分析任务中存在什么问题，再进行架构改造。

## 6.2 Memory Benchmark

建议：

```text
benchmarks/memory/
├── single_fact/
├── multi_turn/
├── cross_session/
├── stale_memory/
├── conflicting_memory/
├── user_preference/
└── procedural_memory/
```

### 示例 1：分析偏好

```text
用户：
以后分析销售额时优先使用月度聚合。

若干轮以后：

问题：
分析这个季度销售额时应该怎么聚合？

Expected：
月度聚合。
```

### 示例 2：指标定义

```text
用户：
这个项目里的 GMV 指支付成功金额，不包括退款。

之后：
GMV 应该怎么计算？
```

### 示例 3：Schema 变化

```text
旧：
sales 表中有 amount 字段。

新：
amount 拆分成 gross_amount / refund_amount。

之后：
当前销售额应该使用哪个字段？
```

### 示例 4：跨 Session

```text
Session A：
订单数据存储在 PostgreSQL。

Session B：
项目的订单数据库是什么？
```

## 6.3 Data Analysis Task Benchmark

同时建立真实的分析任务：

```text
benchmarks/agent/
├── simple_qa/
├── aggregation/
├── filtering/
├── multi_table_join/
├── trend_analysis/
├── anomaly_detection/
├── cohort_analysis/
└── multi_step_analysis/
```

例如：

```text
“过去 6 个月哪个产品线销售增长最快？”

“华东地区 Q2 的复购率是多少？”

“找出本月销售额异常下降的渠道。”

“为什么某产品利润下降了？”
```

## 6.4 指标

### Memory

```text
Memory Recall
Memory Precision
Cross-session Recall
Stale Memory Rate
Conflict Resolution Rate
```

### Agent

```text
Task Success Rate
SQL Success Rate
Tool Call Success Rate
Answer Correctness
Average Steps
Latency
Token Usage
```

## 6.5 阶段产出

```text
benchmarks/
├── memory_dataset.jsonl
├── agent_dataset.jsonl
├── evaluation_config.yaml
└── baseline_report.md
```

## 6.6 阶段验收标准

形成：

```text
Baseline
 ↓
Dataset
 ↓
Metrics
 ↓
Failure Cases
 ↓
Problem Definition
```

最终必须回答：

> 原始 nanobot 在数据分析场景下最值得改造的问题是什么？

---

# 7. Phase 3：Memory Engine 重构

**周期：第 5~6 周**

## 7.1 阶段目标

把 Memory 升级成独立、可插拔、可评测的 Memory Engine。

## 7.2 总体设计

```text
                  MemoryManager
                       │
          ┌────────────┼─────────────┐
          ▼            ▼             ▼
   MemoryWriter   MemoryRetriever   Consolidator
          │            │             │
          └────────────┼─────────────┘
                       ▼
                  MemoryStore
                       │
             ┌─────────┼─────────┐
             ▼         ▼         ▼
          FileStore SQLiteStore VectorStore
```

## 7.3 Task 3.1：定义 Memory Model

```python
class Memory:
    id: str
    content: str
    type: MemoryType
    scope: str
    source: str
    confidence: float
    importance: float
    created_at: datetime
    updated_at: datetime
    status: str
    metadata: dict
```

## 7.4 Task 3.2：Memory 类型

### Episodic Memory

记录：

```text
2026-09-17：
用户完成了一次 Q2 销售分析。
最终发现华东区域异常下降。
```

### Semantic Memory

记录：

```text
业务定义：
GMV 不包含退款。

数据事实：
orders 表是核心订单表。
```

### Procedural Memory

记录：

```text
用户要求销售分析默认：
1. 先按月聚合
2. 再比较同比
3. 最后分析异常点
```

## 7.5 Task 3.3：MemoryStore 抽象

```python
class MemoryStore(ABC):

    async def add(self, memory): ...
    async def get(self, memory_id): ...
    async def search(self, query, top_k=5): ...
    async def update(self, memory_id, memory): ...
    async def delete(self, memory_id): ...
```

建议至少：

```text
FileMemoryStore
SQLiteMemoryStore
```

有余力再做：

```text
VectorMemoryStore
```

## 7.6 Task 3.4：Memory Retrieval

支持：

```text
relevance
importance
freshness
scope
confidence
```

候选流程：

```text
Query
 ↓
Candidate Retrieval
 ↓
Metadata Filter
 ↓
Score
 ↓
Rank
 ↓
Top-K Memory
```

## 7.7 Task 3.5：Memory Consolidation

```text
Raw Conversation
 ↓
Memory Extraction
 ↓
Classification
 ↓
Deduplication
 ↓
Conflict Resolution
 ↓
Importance Scoring
 ↓
Persist
```

## 7.8 Task 3.6：分析偏好与业务知识的长期记忆

特别针对 Data Analyst Agent 保存：

```text
用户常用指标
默认时间粒度
常用筛选条件
常用维度
业务术语定义
历史分析结论
常用数据源
```

例如：

```text
user_preference:
  time_granularity: monthly

business_definition:
  gmv: paid_order_amount_excluding_refund
```

## 7.9 阶段产出

```text
framework/memory/
├── models.py
├── manager.py
├── store.py
├── retrieval.py
├── consolidation.py
├── ranking.py
└── backends/
    ├── file.py
    ├── sqlite.py
    └── vector.py
```

## 7.10 阶段验收标准

- [ ] Memory Store 可替换。
- [ ] Memory 类型可扩展。
- [ ] 支持跨 Session 查询。
- [ ] 支持 Memory 更新与淘汰。
- [ ] 支持基本冲突解决。
- [ ] 能保存用户分析偏好。
- [ ] 能保存业务指标定义。
- [ ] 原有 Memory 功能没有被破坏。
- [ ] Memory Benchmark 有真实对比结果。

---

# 8. Phase 4：Retrieval / RAG Engine

**周期：第 7~8 周前半**

## 8.1 阶段目标

将 Retrieval 从 Data Analyst Application 中抽离出来，形成独立 Retrieval Engine。

Data Analyst Agent 中的 RAG 主要服务于：

```text
指标定义
业务规则
数据字典
字段说明
表关系
分析规范
历史报告
```

## 8.2 总体架构

```text
                    Query
                      │
              ┌───────┴───────┐
              ▼               ▼
       Dense Retrieval   Sparse Retrieval
              │               │
              ▼               ▼
       Vector Results     Keyword Results
              │               │
              └───────┬───────┘
                      ▼
                  Fusion / RRF
                      │
                      ▼
                   Rerank
                      │
                      ▼
                 Top-K Context
```

## 8.3 Task 4.1：数据文档 Ingestion

支持：

```text
Markdown
PDF / 文本文档
Data Dictionary
CSV Metadata
Database Schema
Business Documents
Historical Reports
```

建议统一形成：

```text
Document
 ↓
Chunk
 ↓
Embedding
 ↓
Index
```

## 8.4 Task 4.2：Chunking

支持：

- Fixed-size Chunk
- Recursive Chunk
- Markdown-aware Chunk
- Table-aware Chunk

对于数据分析场景重点保存：

```text
document_id
chunk_id
source
table_name
column_name
section
metadata
```

## 8.5 Task 4.3：Dense Retrieval

```text
Query
 ↓
Embedding
 ↓
Vector DB
 ↓
Top-K
```

## 8.6 Task 4.4：Sparse Retrieval

建议：

```text
BM25
或
SQLite FTS
```

重点解决：

```text
字段名
表名
指标名称
API / SQL 函数
Error Message
专业术语
```

例如：

```text
“净销售额” → net_sales

“复购率” → repeat_customer_rate
```

## 8.7 Task 4.5：Hybrid Retrieval

```text
Dense
+
Sparse
+
RRF / Score Fusion
```

## 8.8 Task 4.6：Reranker

```text
Recall Top 20
 ↓
Rerank
 ↓
Top 5
```

## 8.9 阶段产出

```text
framework/retrieval/
├── base.py
├── dense.py
├── sparse.py
├── hybrid.py
├── reranker.py
├── fusion.py
├── chunking.py
└── ingestion.py
```

## 8.10 阶段验收标准

完成以下实验：

```text
Dense only
Sparse only
Hybrid
Hybrid + Rerank
```

比较：

```text
Recall@K
MRR
nDCG
Latency
```

---

# 9. Phase 5：Context Manager 重构

**周期：第 8 周后半**

## 9.1 阶段目标

解决：

> “检索到了什么”不等于“最终应该给 LLM 什么”。

Data Analyst Agent 经常需要同时处理：

```text
System Prompt
+
用户问题
+
数据库 Schema
+
Memory
+
RAG
+
SQL Result
+
Python Result
+
Tool State
```

因此必须建立独立 Context Manager。

## 9.2 架构

```text
Memory
   │
   ▼
Retriever
   │
   ▼
Ranker
   │
   ▼
Context Manager
   ├── relevance filtering
   ├── deduplication
   ├── freshness
   ├── priority
   ├── token budget
   └── result compression
   │
   ▼
LLM Context
```

## 9.3 Task 5.1：Context Budget

示例：

```text
Context Budget = 10K

System Prompt      1.5K
Recent History     2.0K
Memory             1.0K
Business RAG       2.0K
Schema             2.0K
Tool Results       1.5K
```

## 9.4 Task 5.2：Context Priority

建议：

```text
System
 >
Current Task
 >
Current Schema
 >
Recent Tool Results
 >
Relevant Memory
 >
Relevant RAG
 >
Low-priority History
```

## 9.5 Task 5.3：Schema Context 压缩

不要每次将整个数据库 Schema 塞入 Prompt：

```text
Database
 ↓
Schema Retrieval
 ↓
Relevant Tables
 ↓
Relevant Columns
 ↓
LLM
```

例如问题：

```text
“分析过去 6 个月华东销售趋势”
```

只注入可能相关的：

```text
orders
customers
regions
products
```

而不是整个数据库。

## 9.6 Task 5.4：Tool Result Context

SQL 返回大量数据时：

```text
SQL Result
 ↓
Summary / Sampling
 ↓
Context
```

避免把几十万行数据直接塞给 LLM。

## 9.7 阶段产出

```text
framework/context/
├── manager.py
├── budget.py
├── ranking.py
├── compression.py
├── schema_context.py
└── models.py
```

## 9.8 阶段验收标准

- [ ] 支持 Context Token Budget。
- [ ] 支持 Schema 动态筛选。
- [ ] 支持 Tool Result 压缩。
- [ ] 支持 Memory / RAG 去重。
- [ ] Context 超限不会直接导致异常。
- [ ] 记录 Context 构建耗时和 Token 使用。

---

# 10. Phase 6：Tool Layer 与数据工具

**周期：第 9 周前半**

## 10.1 阶段目标

增强 nanobot Tool 系统，使其能够支持完整的数据分析工作流。

## 10.2 总体 Tool 架构

```text
                    Agent
                      │
                 Tool Router
                      │
        ┌─────────────┼─────────────┐
        ▼             ▼             ▼
     Data Tools    Analysis Tools  Support Tools
        │             │             │
   ┌────┼────┐       ┌┴────┐       ├── Search
   ▼    ▼    ▼       ▼     ▼       ├── File
 SQL Schema CSV      Python Chart   └── Docs
```

## 10.3 Task 6.1：SQL Tool

支持：

```text
connect
schema
describe_table
execute_query
```

要求：

- SQL 参数校验。
- Query Timeout。
- Read-only 模式。
- 错误捕获。
- 结果行数限制。
- 基本安全控制。

## 10.4 Task 6.2：Schema Tool

支持：

```text
list_databases
list_tables
describe_table
foreign_keys
search_columns
```

目标：

> 帮助 Agent 在生成 SQL 前先理解数据库结构。

## 10.5 Task 6.3：Python Analysis Tool

提供受控 Python 环境：

```text
Pandas
NumPy
Matplotlib
```

主要用于：

```text
统计分析
聚合
趋势分析
异常检测
相关性分析
简单建模
```

## 10.6 Task 6.4：File Tool

支持：

```text
CSV
XLSX
JSON
```

Agent 可以：

```text
读取文件
查看 Schema
采样数据
执行分析
生成结果
```

## 10.7 Task 6.5：Chart Tool

支持：

```text
line chart
bar chart
pie chart
histogram
scatter plot
```

基本流程：

```text
Analysis
 ↓
Chart Specification
 ↓
Chart Tool
 ↓
Image
 ↓
Agent Explanation
```

## 10.8 阶段产出

```text
framework/tools/
├── base.py
├── router.py
├── sql.py
├── schema.py
├── python.py
├── file.py
└── chart.py
```

## 10.9 阶段验收标准

完成至少：

- [ ] SQL 查询。
- [ ] Schema 查询。
- [ ] CSV / Excel 读取。
- [ ] Pandas 分析。
- [ ] 基础图表生成。
- [ ] Tool Error 能被 Agent 感知并恢复。

---

# 11. Phase 7：Evaluation 与 Observability

**周期：第 9 周后半**

## 11.1 阶段目标

把系统从“能跑”提升到：

> **可评测、可分析、可优化。**

## 11.2 Evaluation Pipeline

```text
Dataset
   ↓
Agent Runner
   ↓
Trace
   ↓
Evaluator
   ↓
Metrics
   ↓
Report
```

## 11.3 Task 7.1：统一 Trace

记录：

```text
request_id
session_id
query
memory_hits
rag_hits
schema_context
tool_calls
sql
python_execution
llm_calls
tokens
latency
final_answer
```

## 11.4 Task 7.2：Memory Evaluation

指标：

```text
Memory Recall
Memory Precision
Cross-session Recall
Stale Memory Rate
Conflict Resolution Rate
```

## 11.5 Task 7.3：Retrieval Evaluation

指标：

```text
Recall@K
Precision@K
MRR
nDCG
```

## 11.6 Task 7.4：SQL Evaluation

增加数据分析场景独有的指标：

```text
SQL Execution Success Rate
SQL Result Correctness
Schema Linking Accuracy
```

## 11.7 Task 7.5：Agent Evaluation

指标：

```text
Task Success Rate
Tool Call Success Rate
SQL Success Rate
Answer Correctness
Average Steps
Latency
Token Usage
```

## 11.8 Task 7.6：Ablation Study

至少比较：

```text
A. 原始 nanobot

B. + Memory Engine

C. + Dense Retrieval

D. + Hybrid Retrieval

E. + Hybrid + Reranker

F. + Context Manager

G. + Improved Tool Layer
```

目标不是证明“新方案一定更好”，而是通过数据分析：

- 哪个模块有效。
- 哪种任务受益最大。
- 哪些场景仍然存在问题。
- 哪些改造带来额外延迟或 Token 成本。

## 11.9 阶段产出

```text
framework/evaluation/
├── evaluator.py
├── metrics.py
├── runner.py
├── traces.py
└── report.py

benchmarks/
└── reports/
    ├── memory_baseline.md
    ├── retrieval_ablation.md
    ├── sql_agent_eval.md
    └── end_to_end_eval.md
```

## 11.10 阶段验收标准

能够用真实实验回答：

> “你的 Framework 重构解决了什么问题？”

并能够给出：

```text
Baseline
vs
New Framework
```

的指标变化。

---

# 12. Phase 8：Data Analyst Agent

**周期：第 10~11 周**

## 12.1 阶段目标

使用前面重构完成的 Framework 构建真正的数据分析 Agent，而不是重新写一套独立 Agent。

## 12.2 应用总体架构

```text
                         User
                           │
                           ▼
                  Data Analyst Agent
                           │
                  ┌────────┴────────┐
                  ▼                 ▼
               Planner           Memory
                  │                 │
                  │          ┌──────┼───────┐
                  │          ▼      ▼       ▼
                  │       Semantic Episodic Procedural
                  │
          ┌───────┼─────────────────────────────┐
          ▼       ▼             ▼               ▼
       Schema    SQL          Python          RAG
       Tool      Tool          Tool           Tool
          │       │             │               │
          └───────┴─────────────┴───────────────┘
                           │
                           ▼
                    Context Manager
                           │
                           ▼
                          LLM
                           │
                           ▼
                    Answer / Report
```

---

## 12.3 核心工作流

### Workflow A：简单数据问答

```text
用户问题
 ↓
识别数据源
 ↓
Schema Retrieval
 ↓
生成 SQL
 ↓
SQL Validation
 ↓
执行 SQL
 ↓
结果解释
 ↓
回答
```

示例：

```text
“上个月销售额是多少？”
```

---

### Workflow B：趋势分析

```text
用户问题
 ↓
Planning
 ↓
Schema Retrieval
 ↓
SQL
 ↓
Execute
 ↓
Python Analysis
 ↓
Trend Detection
 ↓
Chart
 ↓
Narrative
```

示例：

```text
“过去 12 个月哪个产品增长最快？”
```

---

### Workflow C：异常分析

```text
Question
 ↓
Retrieve Metrics Definition
 ↓
Query Historical Data
 ↓
Python Analysis
 ↓
Detect Anomaly
 ↓
Retrieve Related Business Context
 ↓
Explain Possible Causes
 ↓
Report
```

示例：

```text
“为什么本月华东区销售额突然下降？”
```

---

### Workflow D：复杂多步骤分析

```text
Question
 ↓
Planner
 ↓
Task Decomposition
 ├── Find tables
 ├── Query data
 ├── Aggregate
 ├── Compare periods
 ├── Detect anomaly
 ├── Retrieve business definition
 └── Generate visualization
 ↓
Intermediate Results
 ↓
Validation
 ↓
Final Report
```

---

# 13. Data Analyst Agent 的 Memory 设计

这是整个应用区别于普通 Text-to-SQL Demo 的关键。

## 13.1 用户偏好

例如：

```text
用户偏好：
- 时间粒度默认使用月
- 图表默认使用折线图
- 金额默认保留两位小数
```

## 13.2 业务知识

例如：

```text
GMV：
支付成功金额 - 退款金额

活跃用户：
过去 30 天至少有一次有效行为的用户
```

## 13.3 历史分析

例如：

```text
2026-08：
用户分析过华东销售下滑。

分析结论：
主要原因来自渠道 A。
```

下一次用户：

> “再看看最近华东有没有类似问题。”

Agent 可以召回历史分析。

## 13.4 Procedural Memory

例如：

```text
用户习惯：
销售分析默认：
1. 同比
2. 环比
3. 异常检测
4. 地区拆分
```

---

# 14. Data Analyst Agent 的 RAG 设计

RAG 不负责直接回答所有问题，而主要负责：

```text
业务定义
+
数据字典
+
字段解释
+
表关系
+
分析规范
+
历史报告
```

例如用户问：

> “复购率为什么下降？”

Agent 先通过 RAG 找到：

```text
复购率定义
customers 表
orders 表
有效订单定义
时间窗口定义
```

再通过 SQL + Python 进行真正的数据计算。

因此整体流程：

```text
Business RAG
      +
Schema Retrieval
      +
SQL
      +
Python
      +
Memory
      ↓
Final Answer
```

---

# 15. Data Analyst Agent 的数据源

第一阶段不需要同时支持所有数据库。

推荐渐进式：

### MVP

```text
CSV
SQLite
PostgreSQL
```

### V2

```text
MySQL
Excel
```

### V3

```text
Data Warehouse
BigQuery
Snowflake
ClickHouse
```

---

# 16. Application MVP

第一版不要追求“万能数据分析”。

只实现下面 5 个能力：

### MVP-1：自然语言转 SQL

```text
“过去三个月每个月的订单量是多少？”
```

### MVP-2：多表 Join

```text
“比较不同地区的客单价。”
```

### MVP-3：趋势 / 对比分析

```text
“今年和去年同期相比增长多少？”
```

### MVP-4：异常检测

```text
“找出最近销售额异常的地区。”
```

### MVP-5：图表生成

```text
“把结果画成趋势图。”
```

---

# 17. Application 进阶能力

MVP 完成之后再增加：

```text
自动分析计划
 ↓
SQL
 ↓
数据检查
 ↓
Python
 ↓
统计分析
 ↓
异常检测
 ↓
Visualization
 ↓
Report
```

再进一步加入：

- Query Rewrite。
- SQL Self-Repair。
- Tool Failure Recovery。
- 数据质量检查。
- 分析结果交叉验证。
- 历史分析对比。
- 多轮追问。
- 分析报告持久化。

---

# 18. 数据分析 Agent 的安全与工程约束

这是项目里非常值得做的一部分，因为“让 Agent 执行 SQL / Python”天然需要控制。

## SQL

建议：

```text
Read-only
Query timeout
Row limit
Statement validation
Forbidden statements
```

例如：

```text
禁止：
DROP
DELETE
UPDATE
INSERT
ALTER
TRUNCATE
```

## Python

建议：

```text
Sandbox
Timeout
Memory Limit
Package Allowlist
File Access Restriction
```

## Tool

统一：

```text
Tool Input Validation
Tool Timeout
Tool Error Handling
Tool Result Schema
```

---

# 19. Phase 9：工程化与项目包装

**周期：第 12 周**

## 19.1 项目工程化

补齐：

```text
CI/CD
Unit Test
Integration Test
Docker
Docker Compose
Environment Config
Logging
Error Handling
```

## 19.2 README 结构

```text
# Data Analyst Agent

## Project Motivation
为什么基于 nanobot 二次开发

## Architecture
Framework + Application 架构

## Core Improvements
Memory
Retrieval
Context
Tools

## Data Analyst Agent
功能与工作流

## Benchmark
Baseline vs New Framework

## Demo
核心场景

## Quick Start
启动方式

## Evaluation
评测方法

## Engineering
安全、部署、日志

## Roadmap
未来工作
```

## 19.3 Demo 场景

至少准备：

1. CSV 数据分析。
2. PostgreSQL 数据分析。
3. 多表 Join。
4. 趋势分析 + 图表。
5. 异常检测。
6. 多轮追问。
7. 基于 Memory 记住用户分析偏好。
8. 基于 RAG 理解业务指标定义。

---

# 20. 推荐的 Git Commit 节奏

不要最后一次性提交大量代码。

建议：

```text
commit 01
docs: add nanobot architecture analysis

commit 02
test: add baseline agent scenarios

commit 03
test: add memory benchmark

commit 04
refactor: introduce memory interfaces

commit 05
feat: add semantic memory

commit 06
feat: add episodic memory

commit 07
feat: add procedural memory

commit 08
feat: add memory consolidation

commit 09
feat: add sparse retrieval

commit 10
feat: add hybrid retrieval

commit 11
feat: add reranker

commit 12
refactor: introduce context manager

commit 13
feat: add schema tool

commit 14
feat: add sql tool

commit 15
feat: add python analysis tool

commit 16
feat: add file and chart tools

commit 17
feat: add evaluation pipeline

commit 18
feat: add data analyst agent

commit 19
feat: add multi-step analysis workflow

commit 20
docs: add benchmark and demo
```

这样以后面试时可以通过 Git History 展示技术演进。

---

# 21. 每个阶段都必须形成“证据”

整个项目最重要的一条原则：

> **每完成一个技术改造，就留下可以验证的证据。**

统一使用：

```text
问题
 ↓
Baseline
 ↓
方案
 ↓
实现
 ↓
实验
 ↓
结果
 ↓
结论
```

例如：

### Memory

```text
问题：
跨 Session 中用户的分析偏好难以稳定召回。

Baseline：
原始 Memory。

方案：
MemoryManager + Memory Retrieval + Ranking。

实验：
100 条 Memory QA。

指标：
Recall / Precision / Cross-session Recall。

结果：
真实实验数据。

结论：
哪些场景得到改善，哪些问题仍然存在。
```

### Text-to-SQL

```text
问题：
Agent 生成 SQL 时对 Schema 理解不足。

Baseline：
直接将数据库 Schema 全量注入 Prompt。

方案：
Schema Retrieval + Context Manager。

实验：
100 条 SQL 问题。

指标：
SQL Execution Success Rate
SQL Result Correctness
Token Usage
Latency

结果：
真实实验数据。

结论：
动态 Schema Retrieval 是否减少了上下文冗余并提升了任务表现。
```

---

# 22. 项目最终架构

推荐最终形成：

```text
                         ┌──────────────────────────┐
                         │      Data Analyst Agent  │
                         └────────────┬─────────────┘
                                      │
                              ┌───────▼────────┐
                              │   Agent Runtime │
                              └───────┬────────┘
                                      │
          ┌───────────────────────────┼──────────────────────────┐
          │                           │                          │
          ▼                           ▼                          ▼
   Context Manager              Memory Engine              Tool Engine
          │                           │                          │
   ┌──────┼──────┐          ┌─────────┼─────────┐       ┌───────┼────────┐
   ▼      ▼      ▼          ▼         ▼         ▼       ▼       ▼        ▼
History Memory  RAG      Semantic  Episodic Procedural SQL    Python   Files
   │      │      │          │         │         │       │       │        │
   └──────┼──────┘          └─────────┼─────────┘       └───────┼────────┘
          │                           │                         │
          └───────────────────────────┼─────────────────────────┘
                                      ▼
                              Schema / Data Layer
                                      │
                           ┌──────────┼──────────┐
                           ▼          ▼          ▼
                        SQLite   PostgreSQL    CSV/XLSX
                                      │
                                      ▼
                                     LLM
                                      │
                                      ▼
                             Answer / Chart / Report
```

---

# 23. 最终项目应该具备的技术能力

## Framework

- [ ] 理解 nanobot Agent Runtime。
- [ ] Memory 接口化。
- [ ] Semantic / Episodic / Procedural Memory。
- [ ] Memory Retrieval。
- [ ] Memory Consolidation。
- [ ] Memory Conflict Resolution。
- [ ] Dense Retrieval。
- [ ] Sparse Retrieval。
- [ ] Hybrid Retrieval。
- [ ] Reranking。
- [ ] Context Budget。
- [ ] Context Compression。
- [ ] Tool Abstraction。
- [ ] Trace / Observability。

## Data Agent

- [ ] Schema Retrieval。
- [ ] Text-to-SQL。
- [ ] SQL Execution。
- [ ] SQL Error Recovery。
- [ ] Python / Pandas。
- [ ] Visualization。
- [ ] CSV / Excel。
- [ ] PostgreSQL / SQLite。
- [ ] 多轮分析。
- [ ] Memory。
- [ ] Business RAG。
- [ ] Multi-step Workflow。

## Evaluation

- [ ] Memory Benchmark。
- [ ] Retrieval Benchmark。
- [ ] Text-to-SQL Benchmark。
- [ ] Agent Task Benchmark。
- [ ] Ablation Study。
- [ ] Latency / Token Evaluation。
- [ ] 真实实验结果。

## Engineering

- [ ] Unit Test。
- [ ] Integration Test。
- [ ] Docker。
- [ ] Read-only SQL。
- [ ] Python Sandbox。
- [ ] Logging。
- [ ] Error Handling。
- [ ] README。
- [ ] Architecture Docs。
- [ ] Demo。

---

# 24. 最终简历项目描述

建议最终整理成下面这种风格：

> **Agent Framework 二次开发与 Data Analyst Agent**
>
> 基于开源 Agent Framework nanobot 进行架构重构，围绕长期记忆、知识检索与数据分析任务设计可插拔 Memory Engine，将 Memory 抽象为 Semantic / Episodic / Procedural 三类，并实现 Memory Retrieval、Consolidation、Conflict Resolution；独立构建 Dense + Sparse + Reranking 的 Hybrid Retrieval Pipeline 与 Context Manager，实现动态 Schema Retrieval、Context Budget、结果压缩与去重；重构 Tool Layer，支持 SQL、Schema、Python/Pandas、文件及可视化工具，构建数据分析 Agent 工作流，实现自然语言数据问答、Text-to-SQL、多表分析、趋势/异常分析及图表生成；搭建 Memory / Retrieval / Text-to-SQL / Agent Evaluation Pipeline，通过 Recall@K、MRR、SQL Execution Success Rate、Task Success Rate、Token Usage、Latency 等指标进行离线评测。

> 简历中的具体指标必须使用真实实验结果，不要预先填入未经验证的数据。

---

# 25. 面试主线

整个项目建议围绕以下 6 个问题准备：

### 1. 为什么基于 nanobot 二次开发？

回答重点：

```text
轻量 Agent Runtime
+
已有 Tool / Provider / Memory 扩展机制
+
适合学习 Agent Framework 内部实现
```

### 2. 为什么改 Memory？

围绕：

```text
跨 Session
用户偏好
业务定义
历史分析
Memory Conflict
```

展开。

### 3. 为什么 RAG 不直接塞 Prompt？

解释：

```text
Retrieval ≠ Context Injection
```

引出：

```text
Retriever
Ranker
Context Manager
Token Budget
```

### 4. 为什么数据分析 Agent 需要 Memory？

举例：

```text
用户偏好
+
业务指标定义
+
历史分析
+
分析流程
```

### 5. 为什么 Data Analyst Agent 不只是 Text-to-SQL？

核心：

```text
Text-to-SQL
+
Schema Retrieval
+
SQL Execution
+
Python Analysis
+
Visualization
+
Memory
+
Business RAG
+
Multi-step Planning
```

### 6. 怎么证明你的 Framework 有价值？

使用：

```text
Baseline
vs
New Framework
+
Benchmark
+
Ablation
+
真实任务
```

---

# 26. 最终项目叙事

最终不要把项目描述成：

> “我 fork 了 nanobot，然后加了 SQL 和 RAG。”

而应该形成完整的技术故事：

```text
研究 nanobot
      ↓
理解 Agent Runtime
      ↓
建立原始 Baseline
      ↓
发现长期 Memory / Schema Context / Tool Use 的问题
      ↓
设计 Memory Engine
      ↓
设计 Retrieval Engine
      ↓
设计 Context Manager
      ↓
扩展 SQL / Python / Schema Tool
      ↓
建立 Evaluation Pipeline
      ↓
基于 Framework 构建 Data Analyst Agent
      ↓
用真实数据分析任务验证 Framework
      ↓
完成工程化部署
```

项目最终定位：

> **一个面向数据分析场景的可扩展 Agent Framework，以及基于该 Framework 构建的 Data Analyst Agent。**

这会比“RAG 数据分析机器人”更能体现你的 Framework 设计、Agent Engineering 和实际落地能力。
