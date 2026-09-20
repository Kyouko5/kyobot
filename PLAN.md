# 基于 nanobot 二次开发的个人 Agent Framework 项目计划

## 1. 项目概述

### 1.1 项目目标

基于开源 Agent 框架 **nanobot** 的核心设计与部分实现，完成一次面向个人项目的二次开发与架构重构，最终形成一个具有独立代码结构和核心抽象的轻量级 Agent Framework。

在此 Framework 基础上，进一步开发一个垂直领域 Agent 应用，用于验证 Framework 在实际场景中的可扩展性。

项目最终形成两个层次：

```text
┌─────────────────────────────────────┐
│        Vertical Agent Application   │
│        垂直领域 Agent 应用            │
└──────────────────┬──────────────────┘
                   │
                   ▼
┌─────────────────────────────────────┐
│        Personal Agent Framework     │
│                                     │
│ Agent Loop                          │
│ Context Manager                     │
│ Memory                              │
│ RAG                                 │
│ Tool System                         │
│ Session                             │
│ Model Provider                      │
└──────────────────┬──────────────────┘
                   │
                   ▼
              LLM / Vector DB
```

### 1.2 项目核心目标

重点不在于实现大量功能，而在于：

1. 理解 Agent Framework 的核心运行机制。
2. 将 nanobot 的核心 Agent Runtime 进行重新组织和抽象。
3. 构建自己的 Memory 抽象与长期记忆机制。
4. 构建可插拔的 RAG Pipeline。
5. 对 Context 构建机制进行重构。
6. 通过一个垂直领域 Agent 验证 Framework。
7. 建立简单的 Evaluation Pipeline。
8. 最终形成能够写入简历、能够进行技术面试讲解的完整项目。

---

# 2. 项目范围

## 2.1 第一阶段重点

本项目第一阶段只关注以下核心能力：

```text
Agent Runtime
├── Agent Loop
├── Agent Runner
├── Context Manager
├── Session Manager
├── Tool Registry
└── Model Provider

Memory
├── Working Memory
├── Episodic Memory
├── Semantic Memory
└── Memory Retriever

RAG
├── Document Loader
├── Chunker
├── Embedding
├── Vector Store
├── Retriever
└── Reranker（可选）

Application
└── Vertical Agent
```

## 2.2 暂不重点实现

以下能力暂不作为项目核心：

* Multi-Agent
* MCP 全面支持
* Browser Agent
* Complex Workflow
* Autonomous Planning
* Reflection Agent
* Voice
* 多模态 Agent
* 分布式 Agent Runtime
* 复杂 Web UI
* 大规模生产部署

原则：

> 优先保证核心 Agent Runtime、Memory、RAG 和垂直应用的完整性，而不是堆积大量 Agent Feature。

---

# 3. 总体开发路线

```text
Phase 0
项目准备
    ↓
Phase 1
nanobot 源码理解
    ↓
Phase 2
核心代码迁移
    ↓
Phase 3
Agent Framework 重构
    ↓
Phase 4
Memory 系统改造
    ↓
Phase 5
RAG 系统建设
    ↓
Phase 6
Context 系统重构
    ↓
Phase 7
垂直领域 Agent
    ↓
Phase 8
Evaluation
    ↓
Phase 9
工程化
    ↓
Phase 10
README / Demo / 简历
```

建议周期：

**6～8 周**

如果时间有限，可以压缩到 4～6 周。

---

# Phase 0：项目准备

## 目标

建立开发环境，跑通 nanobot，并建立自己的项目仓库。

## 任务

### 0.1 获取并运行 nanobot

* [x] Clone nanobot
* [x] 安装依赖
* [x] 配置 LLM
* [x] 成功启动 CLI
* [x] 完成一次基础对话
* [x] 完成一次 Tool Calling
* [x] 测试 Memory
* [x] 测试 Session

### 0.2 建立自己的项目

创建独立 Repository：

```text
my-agent-framework/
```

初始目录：

```text
my-agent-framework/
├── README.md
├── PLAN.md
├── pyproject.toml
├── src/
├── tests/
└── docs/
```

结果（2026-09-17）：独立仓库为 `kyobot`（`origin https://github.com/Kyouko5/kyobot.git`），
仓库根目录即工程根，未再嵌套 `my-agent-framework/`（理由见 `docs/decision-records/0001`）。

```text
kyobot/
├── README.md          ✓
├── PLAN.md            ✓
├── pyproject.toml     ✓  （hatchling + ruff/mypy/pytest/coverage 配置）
├── .gitignore         ✓  （排除上游参照 nanobot/、.venv/、缓存与密钥）
├── .editorconfig      ✓
├── .pre-commit-config.yaml ✓
├── src/myagent/       ✓  （含 observability/logging.py 与 py.typed）
├── tests/             ✓  （24 项测试，覆盖率 100%）
├── docs/              ✓  （architecture / development / decision-records / records）
├── scripts/           ✓  （bootstrap.sh / check.sh）
└── nanobot/           ✓  （上游只读参照，git ignored）
```

### 0.3 建立开发规范

* [x] Python 项目规范（`requires-python >= 3.11`；src layout；命名与依赖管理约定，见 `docs/development.md`）
* [x] Git Commit 规范（Conventional Commits + `phase-<N>/<slug>` 分支 + 一个 Task 至少一次提交）
* [x] pytest（`tests/`，`asyncio_mode = "auto"`，`pythonpath = ["src"]`，24 项测试通过）
* [x] Ruff / Black（统一用 ruff：`ruff check` 规则集 `E,F,W,I,N,UP,B,C4,SIM,RUF,ASYNC,ANN` + `ruff format`，行宽 100）
* [x] 类型标注（`py.typed` + `mypy --strict` 覆盖 `src/`）
* [x] 基础 Logging（`src/myagent/observability/logging.py`：命名空间隔离、text/JSON 两种格式、结构化 `extra` 字段）

## 阶段产出

```text
☑ nanobot 可以正常运行          （v0.3.5，`nanobot agent` 交互与单条消息均可用）
☑ 自己的 GitHub Repository      （Kyouko5/kyobot）
☑ 开发环境                      （.venv + ruff/mypy/pytest/pre-commit，scripts/bootstrap.sh 可复现）
☑ 初始 README                   （README.md：定位 / 进度 / 快速开始 / 目录 / 文档索引）
☑ PLAN.md                       （本文件）
☑ 质量门                        （scripts/check.sh：format → lint → type → test + coverage）
```

## 验收标准

能够回答：

> nanobot 如何启动？

> 一条用户消息进入系统后，经过哪些模块？

结论（答案与文件行号见 `docs/architecture.md`）：

1. `nanobot` 命令由 `nanobot.cli.entry:main` 进入并路由到 agent 命令；`cli/agent.py` 依次装配
   config → provider → workspace 模板 → MessageBus → cron → ToolRegistry/MCP → `AgentLoop`，
   之后要么 `process_direct()` 单次执行，要么 `agent_loop.run()` 常驻消费总线。
2. 用户输入 → `InboundMessage` → `MessageBus.inbound` → `AgentLoop`（会话解析、`ContextBuilder` 组装上下文）
   → `AgentRunner` 的「模型 ↔ 工具」循环 → 写回 `SessionManager`（JSONL）与长期 Memory
   → `OutboundMessage` / 流式事件 → `MessageBus.outbound` → 频道渲染。

记录：`docs/records/phase-0-setup.md`

---

# Phase 1：nanobot 源码理解

## 目标

彻底理解 nanobot 的核心 Agent Runtime，而不是直接开始修改代码。

## 重点模块

```text
agent/
├── loop.py
├── runner.py
├── context.py
├── memory.py
└── tools/

session/
bus/
providers/
config/
```

## 任务

### 1.1 理解消息流

画出：

```text
User
 ↓
Message
 ↓
MessageBus
 ↓
AgentLoop
 ↓
AgentRunner
 ↓
LLM
 ↓
Tool
 ↓
Tool Result
 ↓
LLM
 ↓
Final Response
```

### 1.2 理解 AgentLoop

重点分析：

* [ ] AgentLoop 的职责
* [ ] 消息接收方式
* [ ] Session 如何管理
* [ ] Context 如何生成
* [ ] AgentRunner 如何调用
* [ ] Response 如何返回

### 1.3 理解 AgentRunner

重点分析：

* [ ] LLM 调用
* [ ] Tool Calling
* [ ] 多轮 Tool Loop
* [ ] 最大循环次数
* [ ] Tool Error Handling
* [ ] Agent Termination

### 1.4 理解 Tool System

分析：

```text
BaseTool
   ↓
Tool Registry
   ↓
Tool Discovery
   ↓
Tool Execution
```

完成：

* [ ] BaseTool
* [ ] Tool Schema
* [ ] Registry
* [ ] Tool Call
* [ ] Tool Result

### 1.5 理解 Context

分析 Context 中包含：

```text
System Prompt
+
Conversation
+
Memory
+
Tools
+
Other Context
```

### 1.6 理解 Memory

重点分析：

* [ ] Session History
* [ ] Long-term Memory
* [ ] Memory Loading
* [ ] Memory Saving
* [ ] Memory 与 Context 的关系

## 阶段产出

创建：

```text
docs/
├── architecture.md
├── agent-loop.md
├── tool-system.md
├── context.md
└── memory.md
```

## 验收标准

能够不看源码解释：

> 一次 Agent 请求从输入到输出经历了什么？

并能够手动画出完整架构图。

---

# Phase 2：核心代码迁移

## 目标

不要继续直接修改 nanobot，而是开始建立自己的 Framework。

核心原则：

> 迁移核心思想和必要实现，同时重新设计自己的模块边界。

## 2.1 建立 Framework 目录

```text
src/myagent/

├── agent/
│   ├── loop.py
│   ├── runner.py
│   └── context.py
│
├── models/
│   ├── base.py
│   └── openai.py
│
├── tools/
│   ├── base.py
│   ├── registry.py
│   └── builtin/
│
├── memory/
│   ├── base.py
│   └── manager.py
│
├── session/
│   └── manager.py
│
└── config/
    └── schema.py
```

## 2.2 Model 抽象

设计：

```python
class BaseModel:

    def generate(self, messages):
        ...
```

实现至少一个：

```text
OpenAI Compatible Provider
```

要求：

* [ ] LLM 抽象
* [ ] Message Format
* [ ] Tool Calling
* [ ] Streaming（可选）

## 2.3 Tool 抽象

设计：

```python
class BaseTool:

    name: str
    description: str

    def run(self, **kwargs):
        ...
```

实现：

```text
Calculator
Time
Search
File Reader
```

至少 2～3 个 Tool。

## 2.4 Agent Runner

实现：

```text
LLM
 ↓
Response
 ↓
Tool Call?
 ├── No → Final
 │
 └── Yes
      ↓
   Execute Tool
      ↓
   Tool Result
      ↓
      LLM
```

增加：

* [ ] max_iterations
* [ ] Tool timeout
* [ ] Tool error handling
* [ ] termination condition

## 2.5 Agent Loop

完成：

```text
Message
 ↓
Session
 ↓
Context
 ↓
AgentRunner
 ↓
Response
```

## 阶段产出

能够执行：

```bash
myagent chat
```

实现：

```text
User
 ↓
My Agent Framework
 ↓
LLM
 ↓
Tool
 ↓
Response
```

## 验收标准

完全脱离 nanobot Repository 后：

```text
my-agent-framework
```

仍然可以独立运行。

---

# Phase 3：Agent Framework 重构

## 目标

从“代码迁移”升级到“自己的 Framework 设计”。

## 3.1 明确模块职责

最终结构：

```text
Agent
│
├── AgentLoop
├── AgentRunner
├── ContextManager
│
├── SessionManager
├── MemoryManager
├── RAGManager
│
├── ToolRegistry
└── ModelProvider
```

## 3.2 解耦 AgentLoop

AgentLoop 只负责：

```text
Message
 ↓
Session
 ↓
Agent Execution
 ↓
Response
```

不直接处理：

* Vector DB
* Embedding
* Memory Retrieval
* Prompt 拼接细节

## 3.3 解耦 Context

设计：

```python
class ContextManager:

    def build(
        self,
        query,
        session,
        memories,
        rag_context,
        tools
    ):
        ...
```

## 3.4 建立统一接口

建议：

```text
BaseModel
BaseTool
BaseMemory
BaseRetriever
BaseVectorStore
BaseEmbedder
```

## 阶段产出

```text
docs/design.md
```

记录：

* 模块职责
* 接口设计
* 为什么这样设计
* 与原 nanobot 的差异

## 验收标准

能够解释：

> 为什么 AgentLoop 不应该直接负责 RAG？

> 为什么 Memory 和 RAG 应该抽象成独立模块？

> 为什么 Tool 需要 Registry？

---

# Phase 4：Memory 系统改造

## 目标

这是整个项目的核心改造之一。

将简单 Memory 升级成分层 Memory Architecture。

## 4.1 Memory 分类

设计：

```text
MemoryManager
│
├── Working Memory
├── Episodic Memory
├── Semantic Memory
└── Memory Retriever
```

## 4.2 Working Memory

负责当前对话上下文。

例如：

```text
最近 N 轮对话
```

实现：

* [ ] Conversation Buffer
* [ ] Token Limit
* [ ] Context Window Control

## 4.3 Episodic Memory

保存过去发生的事件。

例如：

```text
用户之前阅读过某篇论文。

用户之前询问过 GraphRAG。

用户上一次 Agent 任务的结果。
```

设计：

```python
class EpisodicMemory:

    def add(...)
    def search(...)
```

## 4.4 Semantic Memory

保存长期事实。

例如：

```text
User prefers Python.

User is studying RAG.

User is working on an Agent project.
```

## 4.5 Memory Storage

第一版可以使用：

```text
SQLite
+
Vector DB
```

不需要复杂数据库架构。

决策（2026-09-20，见 ADR-0003）：**SQLite** 存文档、chunk 与元数据（`MYAGENT_SQLITE_PATH`，
默认 `data/myagent.db`）；**Qdrant** 存向量（`MYAGENT_QDRANT_URL`，本地默认 `http://localhost:6333`）。
两者用 `document_id` / `chunk_id` 关联，通过 `SQLiteSettings` / `QdrantSettings` 读取配置，
存储实现不直接读环境变量。

## 4.6 Memory Retrieval

实现：

```text
Query
 ↓
Embedding
 ↓
Vector Search
 ↓
Top-K
 ↓
Memory Context
```

## 4.7 Memory 写入机制

设计：

```text
Conversation
 ↓
Memory Extractor
 ↓
判断是否值得长期保存
 ↓
Memory Store
```

不要把所有聊天内容都写入长期 Memory。

## 阶段产出

```text
docs/
└── memory-design.md
```

包含：

```text
Memory Architecture
Memory Lifecycle
Memory Retrieval
Memory Storage
Memory Injection
```

## 验收标准

至少完成：

```text
Memory OFF
Memory ON
```

对比测试。

例如：

```text
Session 1:
用户：我正在研究 RAG。

Session 2:
用户：我最近研究什么方向？

Memory OFF:
无法回答

Memory ON:
能够正确召回相关信息
```

---

# Phase 5：RAG 系统建设

## 目标

实现一个简单、模块化的 RAG Pipeline。

## 5.1 RAG Architecture

```text
Document
 ↓
Loader
 ↓
Chunker
 ↓
Embedding
 ↓
Vector Store
 ↓
Retriever
 ↓
Reranker
 ↓
Context
 ↓
LLM
```

## 5.2 Document Loader

第一版支持：

```text
PDF
TXT
Markdown
```

## 5.3 Chunker

实现：

```text
Fixed Size Chunk
```

后续可增加：

```text
Recursive Chunk
```

## 5.4 Embedding

抽象：

```python
class BaseEmbedder:

    def embed(self, texts):
        ...
```

决策（2026-09-20，见 ADR-0005）：默认使用**阿里云 DashScope**（`EMBED_MODEL_TYPE=dashscope`，
`EMBED_MODEL_NAME=qwen3.7-text-embedding-flash`，OpenAI 兼容模式）。配置项为
`EMBED_MODEL_TYPE` / `EMBED_MODEL_NAME` / `EMBED_API_KEY` / `EMBED_BASE_URL` /（可选）`EMBED_DIM`，
由 `EmbeddingSettings` 读取；`EMBED_DIM` 留空时在 Phase 5 用一次真实调用探测维度，并作为
Qdrant collection 的向量维度。`EMBED_MODEL_TYPE` 允许切到 `openai` 作为对比基线。

## 5.5 Vector Store

抽象：

```python
class BaseVectorStore:

    def add(...)
    def search(...)
```

第一版使用：

```text
Qdrant
```

决策（2026-09-20，见 ADR-0003）：选 **Qdrant** 而不是 Chroma / FAISS —— 前者是完整向量数据库
（HNSW + payload 过滤 + 持久化），后两者分别是本地库与索引文件，在生产形态与过滤语义上更弱。
本地用 docker 启动，云端只需 url + api key，配置形状一致；`qdrant-client` 依赖在 Phase 5 引入。

## 5.6 Retriever

实现：

```python
retriever.retrieve(
    query,
    top_k=5
)
```

## 5.7 Reranker

第一版可选。

如果时间允许：

```text
Retriever
 ↓
Top 10
 ↓
Reranker
 ↓
Top 3
```

## 阶段产出

```text
docs/rag-design.md
```

以及：

```text
tests/rag/
```

## 验收标准

可以：

```text
上传 PDF
 ↓
建立知识库
 ↓
提出问题
 ↓
检索相关内容
 ↓
LLM 回答
```

---

# Phase 6：Context Manager 重构

## 目标

将 Memory 和 RAG 正式接入 Agent Context。

最终 Context：

```text
System Instruction
        +
User Profile
        +
Recent Conversation
        +
Relevant Memory
        +
RAG Context
        +
Available Tools
        ↓
Context Manager
        ↓
LLM
```

## 6.1 Context Priority

设计优先级：

```text
System Prompt
      ↓
Current Query
      ↓
Recent Conversation
      ↓
Relevant Memory
      ↓
RAG Context
      ↓
Tool Information
```

## 6.2 Context Budget

增加：

```text
max_context_tokens
```

不同来源设置预算：

```text
Conversation: 30%
Memory:      20%
RAG:         40%
Other:       10%
```

具体比例根据实际测试调整。

## 6.3 Context Compression

第一版可以实现简单策略：

```text
Recent Conversation
+
Summary of Old Conversation
```

而不是无限增加历史消息。

## 阶段产出

完整：

```text
ContextManager
MemoryManager
RAGManager
```

形成统一 Context Pipeline。

## 验收标准

Agent 可以同时使用：

```text
Conversation
+
Memory
+
RAG
+
Tools
```

---

# Phase 7：垂直领域 Agent

## 目标

基于自己的 Agent Framework 构建一个真正的 Agent Application。

建议项目：

# Research Agent

主要面向：

```text
论文阅读
技术资料研究
知识库问答
```

## 7.1 核心功能

### PDF 分析

```text
Upload PDF
 ↓
Parse
 ↓
Chunk
 ↓
Embedding
 ↓
Vector DB
```

### 论文问答

例如：

```text
这篇论文解决了什么问题？

作者提出了什么方法？

实验结果如何？

它和 RAG 有什么区别？
```

### 多论文比较

```text
Paper A
+
Paper B
 ↓
RAG
 ↓
Agent
 ↓
Comparison
```

### 长期 Memory

例如：

```text
用户之前研究过：

RAG
GraphRAG
Agent Memory
```

当用户再次提问时：

```text
Query
 ↓
Memory Retrieval
 ↓
Paper Retrieval
 ↓
LLM
```

## 7.2 Tools

实现：

```text
search_paper
read_paper
save_note
search_memory
```

## 7.3 Agent Flow

```text
User Query
     │
     ▼
Research Agent
     │
     ├──── Memory Retrieval
     │
     ├──── Paper RAG
     │
     ├──── Tool Calling
     │
     └──── LLM
              │
              ▼
           Answer
```

## 阶段产出

```text
examples/
└── research_agent/
```

包含：

```text
agent.py
tools.py
prompts.py
README.md
```

## 验收标准

至少完成以下 Demo：

```text
Demo 1：单论文问答

Demo 2：多论文比较

Demo 3：跨 Session Memory

Demo 4：RAG + Tool Calling
```

---

# Phase 8：Evaluation

## 目标

证明 Framework 的改造确实产生了效果，而不是单纯增加代码。

## 8.1 建立 Evaluation Dataset

准备：

```text
20～50 个测试问题
```

格式：

```json
{
    "question": "...",
    "expected_answer": "...",
    "source": "..."
}
```

## 8.2 Memory Evaluation

比较：

```text
Memory OFF
vs
Memory ON
```

指标：

```text
Memory Retrieval Hit Rate
Answer Accuracy
```

## 8.3 RAG Evaluation

比较：

```text
RAG OFF
vs
RAG ON
```

指标：

```text
Retrieval Hit Rate
Answer Accuracy
```

## 8.4 Retrieval Evaluation

测试：

```text
Top-K = 3
Top-K = 5
Top-K = 10
```

记录：

```text
Hit Rate
Latency
```

## 8.5 Agent Evaluation

记录：

```text
平均执行时间
平均 Token 使用量
Tool Call 次数
Agent Loop 次数
最终回答准确率
```

## 阶段产出

```text
evaluation/
├── dataset.json
├── evaluate.py
├── results.json
└── README.md
```

最终生成：

```text
docs/evaluation.md
```

## 验收标准

能够回答：

> 你的 Memory 为什么有效？

> RAG 加入以后有什么变化？

> Retrieval 的 Top-K 为什么选择这个值？

---

# Phase 9：工程化

## 目标

让项目从“学习项目”变成“可以展示的工程项目”。

## 9.1 Logging

记录：

```text
Agent Start
LLM Call
Tool Call
Memory Retrieval
RAG Retrieval
Agent Finish
```

例如：

```text
[Agent] start
[Memory] retrieved 3 memories
[RAG] retrieved 5 chunks
[LLM] tool call: search_paper
[Tool] finished
[Agent] completed
```

## 9.2 Configuration

统一：

```yaml
model:
  provider: openai
  model: xxx

memory:
  enabled: true
  top_k: 5

rag:
  enabled: true
  top_k: 5

agent:
  max_iterations: 10
```

## 9.3 Docker

提供：

```text
Dockerfile
docker-compose.yml
```

至少可以启动：

```bash
docker compose up
```

## 9.4 Tests

至少覆盖：

```text
tests/
├── test_agent.py
├── test_tools.py
├── test_memory.py
├── test_rag.py
└── test_context.py
```

重点测试：

* [ ] Agent Loop
* [ ] Tool Calling
* [ ] Memory Retrieval
* [ ] RAG Retrieval
* [ ] Context Construction

---

# Phase 10：README / Demo / 简历包装

## 目标

把项目从“代码”包装成一个完整的工程作品。

## 10.1 README 结构

```text
# MyAgent

## Introduction

## Features

## Architecture

## Quick Start

## Agent Runtime

## Memory

## RAG

## Research Agent

## Evaluation

## Project Structure

## Design Decisions

## Future Work

## Acknowledgements
```

## 10.2 Architecture Diagram

最终 README 至少展示：

```text
                 User
                  │
                  ▼
            ┌───────────┐
            │ AgentLoop │
            └─────┬─────┘
                  │
          ┌───────▼────────┐
          │ ContextManager │
          └───────┬────────┘
                  │
       ┌──────────┼──────────┐
       ▼          ▼          ▼
    Memory       RAG       Tools
       │          │          │
       └──────────┼──────────┘
                  ▼
                LLM
                  │
                  ▼
              Response
```

## 10.3 Demo GIF / 视频

准备一个 1～3 分钟 Demo：

```text
1. 启动 Agent
2. 上传论文
3. 询问论文内容
4. Agent 进行 RAG
5. 调用 Tool
6. 保存 Memory
7. 新 Session
8. 再次询问
9. 展示 Memory Recall
```

## 10.4 技术亮点

README 中重点突出：

```text
✓ Modular Agent Runtime
✓ Tool Calling
✓ Layered Memory
✓ Long-term Memory Retrieval
✓ RAG Pipeline
✓ Context Management
✓ Vertical Agent
✓ Evaluation
```

---

# 4. 最终项目目录

最终建议形成：

```text
my-agent-framework/
│
├── README.md
├── PLAN.md
├── LICENSE
├── pyproject.toml
├── Dockerfile
├── docker-compose.yml
│
├── src/
│   └── myagent/
│       │
│       ├── agent/
│       │   ├── loop.py
│       │   ├── runner.py
│       │   └── context.py
│       │
│       ├── models/
│       │   ├── base.py
│       │   └── openai.py
│       │
│       ├── tools/
│       │   ├── base.py
│       │   ├── registry.py
│       │   └── builtin/
│       │
│       ├── memory/
│       │   ├── base.py
│       │   ├── manager.py
│       │   ├── working.py
│       │   ├── episodic.py
│       │   ├── semantic.py
│       │   └── retriever.py
│       │
│       ├── rag/
│       │   ├── document.py
│       │   ├── loader.py
│       │   ├── chunker.py
│       │   ├── embedder.py
│       │   ├── vectorstore.py
│       │   ├── retriever.py
│       │   └── pipeline.py
│       │
│       ├── session/
│       │   └── manager.py
│       │
│       └── config/
│           └── schema.py
│
├── examples/
│   └── research_agent/
│       ├── agent.py
│       ├── tools.py
│       └── README.md
│
├── tests/
│   ├── test_agent.py
│   ├── test_tools.py
│   ├── test_memory.py
│   ├── test_rag.py
│   └── test_context.py
│
├── evaluation/
│   ├── dataset.json
│   ├── evaluate.py
│   └── results.json
│
└── docs/
    ├── architecture.md
    ├── design.md
    ├── memory-design.md
    ├── rag-design.md
    ├── evaluation.md
    └── decision-records/
```

---

# 5. 每阶段核心产出

| 阶段       | 核心产出             | 验收标准                     |
| -------- | ---------------- | ------------------------ |
| Phase 0  | 开发环境             | nanobot 跑通               |
| Phase 1  | 源码分析文档           | 能讲清 Agent Runtime        |
| Phase 2  | 自己的 Framework V1 | 独立运行 Agent               |
| Phase 3  | Framework 重构     | 模块职责清晰                   |
| Phase 4  | Memory V2        | 跨 Session 记忆             |
| Phase 5  | RAG Pipeline     | PDF → Retrieval → Answer |
| Phase 6  | Context Manager  | Memory + RAG + Tools     |
| Phase 7  | Research Agent   | 完成真实 Demo                |
| Phase 8  | Evaluation       | 有量化实验结果                  |
| Phase 9  | 工程化              | Test + Docker + Logging  |
| Phase 10 | 项目包装             | GitHub + Demo + 简历       |

---

# 6. MVP 版本定义

如果时间不足，不要继续增加功能。

优先完成以下 MVP：

```text
                    MyAgent
                       │
        ┌──────────────┼──────────────┐
        ▼              ▼              ▼
     Agent          Memory           RAG
        │              │              │
        ▼              ▼              ▼
   Tool Calling    Long-term       PDF Search
        │           Memory             │
        └──────────────┼───────────────┘
                       ▼
                    LLM
                       │
                       ▼
                Research Agent
```

MVP 必须具备：

* [ ] Agent Loop
* [ ] Tool Calling
* [ ] Session
* [ ] Memory
* [ ] Vector Retrieval
* [ ] RAG
* [ ] Context Manager
* [ ] Research Agent
* [ ] 基础 Evaluation

其他功能全部属于 Nice to Have。

---

# 7. 后续可选方向

在 MVP 完成以后，再考虑：

## 7.1 Hybrid Retrieval

```text
BM25
+
Vector Search
↓
Fusion
↓
Rerank
```

## 7.2 Query Rewrite

```text
User Query
 ↓
Query Rewriter
 ↓
Retriever
```

## 7.3 Memory Consolidation

```text
Episodic Memory
       ↓
Memory Summarizer
       ↓
Semantic Memory
```

## 7.4 Agent Observability

增加：

```text
Trace
 ├── LLM Call
 ├── Tool Call
 ├── Memory Retrieval
 ├── RAG Retrieval
 └── Token Usage
```

## 7.5 Multi-Agent

只有在单 Agent Runtime 稳定以后再考虑：

```text
Research Agent
      │
      ├── Search Agent
      ├── Paper Agent
      └── Writer Agent
```

---

# 8. 项目开发原则

## 原则一：不要追求功能数量

项目核心不是：

```text
10 个 Agent Feature
```

而是：

```text
3～5 个核心模块
+
清晰的架构
+
真实的技术改造
+
Evaluation
```

## 原则二：不要直接复制整个 nanobot

采用：

```text
理解
 ↓
迁移
 ↓
重新抽象
 ↓
功能增强
 ↓
测试
```

而不是：

```text
Fork
 ↓
改名
 ↓
改几个文件
```

## 原则三：每次重构都要有理由

例如：

```text
为什么 Memory 独立？

为什么 ContextManager 独立？

为什么 Retriever 抽象？

为什么 Tool 使用 Registry？

为什么 Memory 和 RAG 分开？

为什么需要 Session？

为什么需要 AgentRunner？
```

这些问题最终都会成为面试问题。

## 原则四：优先保证可解释性

整个项目最终应该能够被你用一张架构图解释清楚。

## 原则五：所有核心改动都要有实验

例如：

```text
Memory ON / OFF
RAG ON / OFF
Top-K 3 / 5 / 10
不同 Chunk Size
不同 Retrieval Strategy
```

---

# 9. 最终简历项目定位

项目名称：

> **MyAgent —— Modular Agent Runtime & Research Agent**

项目定位：

> 面向知识密集型任务的轻量级 Agent Framework。

核心技术：

```text
Python
LLM
Agent Runtime
Tool Calling
RAG
Vector Retrieval
Long-term Memory
Context Management
Evaluation
```

简历项目描述建议最终围绕以下三个方向：

### Framework

> 重新设计并实现轻量级模块化 Agent Runtime，抽象 Agent Loop、Agent Runner、Tool Registry、Model Provider、Session 和 Context Manager，实现模型调用、工具编排及上下文管理解耦。

### Memory / RAG

> 设计分层 Memory Architecture，将 Working、Episodic 和 Semantic Memory 解耦，并基于向量检索实现长期记忆召回；同时构建模块化 RAG Pipeline，支持文档解析、Chunk、Embedding、Vector Retrieval 和 Reranking。

### Application / Evaluation

> 基于自研 Agent Runtime 构建 Research Agent，实现论文知识库问答、跨 Session Memory、Tool Calling 和多文档检索，并建立离线 Evaluation Pipeline，从 Retrieval Hit Rate、Answer Accuracy 和 Latency 等指标评估系统效果。

---

# 10. 最终完成标准

当以下问题你都能够回答时，项目基本达到简历项目要求：

### Agent

* [ ] Agent Loop 是什么？
* [ ] Agent Runner 和 Agent Loop 为什么分开？
* [ ] Tool Calling 如何实现？
* [ ] 如何限制 Agent 无限循环？
* [ ] Tool Error 如何处理？

### Context

* [ ] Context 是怎么构建的？
* [ ] Conversation、Memory、RAG 如何进入 Context？
* [ ] Context 太长怎么办？
* [ ] 如何进行 Context Compression？

### Memory

* [ ] Short-term Memory 和 Long-term Memory 有什么区别？
* [ ] Episodic Memory 和 Semantic Memory 为什么要区分？
* [ ] 什么信息应该写入 Memory？
* [ ] Memory 如何检索？
* [ ] 如何避免 Memory 污染？

### RAG

* [ ] 为什么需要 Chunk？
* [ ] Chunk Size 怎么选择？
* [ ] Embedding 做什么？
* [ ] Vector Search 怎么工作？
* [ ] Top-K 怎么选择？
* [ ] Retriever 和 Reranker 有什么区别？
* [ ] RAG 为什么会召回错误内容？

### Framework

* [ ] 为什么需要 BaseModel？
* [ ] 为什么 Tool 需要 Registry？
* [ ] 为什么 RAG 不直接写在 AgentLoop？
* [ ] 如何扩展新的 Model？
* [ ] 如何扩展新的 Tool？
* [ ] 如何扩展新的 Memory Store？

### Engineering

* [ ] 如何测试 Agent？
* [ ] 如何评价 RAG？
* [ ] 如何评价 Memory？
* [ ] 如何降低 Token 消耗？
* [ ] 如何降低 Agent Latency？

---

# 11. 项目最终目标

最终不是为了证明：

> “我改过 nanobot。”

而是为了证明：

> **我理解一个 Agent Framework 的核心运行机制，并能够从现有开源实现中提取核心设计，重新进行模块抽象，在此基础上实现 Memory、RAG 和 Context 等能力，并最终将 Framework 应用于实际垂直场景。**

项目最终形成：

```text
          nanobot
             │
             │ 学习 / 迁移
             ▼
      My Agent Framework
             │
      ┌──────┼───────┐
      ▼      ▼       ▼
    Agent  Memory    RAG
      │      │       │
      └──────┼───────┘
             ▼
       Context Manager
             │
             ▼
            LLM
             │
             ▼
      Research Agent
             │
             ▼
        Evaluation
             │
             ▼
     可展示的工程项目
```

**最终评价项目的标准不是代码量，而是：架构是否清晰、核心模块是否真正理解、改造是否有技术理由、实验是否能够验证设计，以及你能否完整讲清楚整个 Agent Runtime。**
