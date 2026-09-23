# MyAgent：基础模式 + 可选 Memory / RAG 改造计划

> 基于当前仓库 `Kyouko5/kyobot` 的 `main` 分支代码整理。  
> 本阶段只解决一个问题：**让别人只配置 LLM 就能直接使用 MyAgent；Memory / RAG 作为按需启用的增强能力。**

## 当前代码现状

当前项目已经具备大部分基础条件，不需要再重构 EmbeddingProvider 或 VectorStore：

- `src/myagent/runtime.py`
  - `build_agent()` 已经统一装配 Agent、Memory、RAG。
  - `build_memory()` / `build_rag()` 会创建 Embedding 和 Qdrant 相关对象。
  - 这些对象目前都是**懒连接**，仅创建对象不会立刻访问 Embedding API 或 Qdrant。
- `src/myagent/rag/embedder.py`
  - 已有 `BaseEmbedder` Protocol。
  - 已支持 DashScope / OpenAI-compatible Embedding。
  - API Key 在真正调用 Embedding 时才通过 `require_api_key()` 检查。
- `src/myagent/rag/vectorstore.py`
  - 已有 `BaseVectorStore` Protocol 和 `QdrantVectorStore`。
- `src/myagent/memory/vector_index.py`
  - 已有 `MemoryIndex` Protocol 和 `QdrantMemoryIndex`。
- `src/myagent/config/settings.py`
  - 本地 Qdrant 默认地址已经是 `http://localhost:6333`。
  - `MYAGENT_QDRANT_API_KEY` 已经允许为空，本地部署不需要 API Key。
  - 当前 `DEFAULT_MEMORY_ENABLED=True`。
  - 当前 `DEFAULT_RAG_ENABLED=True`。
- `src/myagent/memory/retriever.py`
  - Memory 在 Embedding / Qdrant 不可用时已经支持关键词降级。
- `src/myagent/rag/pipeline.py`
  - `MYAGENT_RAG_ENABLED=false` 时 `recall()` 已经可以直接返回空结果。
  - 但 RAG 开启后，如果 Embedding 或 Qdrant 出错，目前可能继续向 Agent 主流程抛异常。

因此本阶段**不增加新的架构层**，只调整默认行为、异常处理、配置说明和测试。

---

# 阶段任务：将 Memory / RAG 改造成真正的可选增强能力

## 任务 1：默认关闭 Memory 和 RAG

修改：

```text
src/myagent/config/settings.py
```

将：

```python
DEFAULT_MEMORY_ENABLED = True
DEFAULT_RAG_ENABLED = True
```

修改为：

```python
DEFAULT_MEMORY_ENABLED = False
DEFAULT_RAG_ENABLED = False
```

同时修改：

```text
.env.example
```

默认配置改为：

```env
MYAGENT_MEMORY_ENABLED=false
MYAGENT_RAG_ENABLED=false
```

### 目标

用户第一次下载项目后，只需要配置：

```env
LLM_BASE_URL=...
LLM_API_KEY=...
LLM_MODEL=...
```

即可：

```bash
myagent chat
myagent web
```

此时：

```text
Agent
├── LLM
├── Tool Calling
├── Session
├── Context Manager
└── Web Gateway
```

都可以正常工作。

不会调用：

```text
Embedding API
Qdrant
```

---

## 任务 2：保留当前懒加载设计，不额外重构 Runtime

当前：

```text
build_agent()
  ├── build_memory()
  └── build_rag()
```

虽然仍然会构造 `MemoryManager` 和 `RagPipeline`，但当前实现中的：

```text
OpenAICompatEmbedder
QdrantMemoryIndex
QdrantVectorStore
```

都采用懒初始化。

因此本阶段**不需要**把 `build_agent()` 改成大量：

```python
if memory.enabled:
    ...
if rag.enabled:
    ...
```

只需要保证：

```text
Memory OFF
→ recall / observe 直接退出

RAG OFF
→ recall 直接返回 []
```

即可。

这样可以保留你现在统一的 Runtime 装配结构，不为了“可选功能”增加新的复杂度。

---

## 任务 3：RAG 增加 Agent 运行时降级

修改：

```text
src/myagent/rag/pipeline.py
```

当前 Memory 已经具备：

```text
Embedding / Qdrant 异常
        ↓
keyword fallback
        ↓
Agent 继续运行
```

RAG 也需要遵循类似原则。

只针对 Agent 使用的：

```python
RagPipeline.recall()
```

增加异常捕获。

建议处理：

```text
EmbeddingError
VectorStoreError
```

行为：

```text
RAG recall
    ↓
Embedding / Qdrant 异常
    ↓
记录 warning
    ↓
return []
    ↓
本轮不注入 RAG Context
    ↓
Agent 继续回答
```

注意：

```python
RagPipeline.retrieve()
```

不要吞掉异常。

因为：

```bash
myagent search "xxx"
```

属于用户主动执行的 RAG 检索命令，此时服务不可用应该明确提示错误。

也就是说：

```text
Agent 自动召回失败
→ 优雅降级

CLI 主动 search / ingest 失败
→ 明确报错
```

---

## 任务 4：明确高级能力的配置要求

保持当前配置体系，不新增 Provider 抽象。

### 基础模式

只需要：

```env
LLM_BASE_URL=...
LLM_API_KEY=...
LLM_MODEL=...

MYAGENT_MEMORY_ENABLED=false
MYAGENT_RAG_ENABLED=false
```

不需要：

```text
EMBED_API_KEY
Qdrant API Key
运行中的 Qdrant
```

---

### Memory / RAG 模式

用户开启：

```env
MYAGENT_MEMORY_ENABLED=true
MYAGENT_RAG_ENABLED=true
```

后，再配置 Embedding：

```env
EMBED_MODEL_TYPE=dashscope
EMBED_MODEL_NAME=qwen3.7-text-embedding-flash
EMBED_API_KEY=...
```

以及 Qdrant：

```env
MYAGENT_QDRANT_URL=http://localhost:6333
```

本地 Qdrant：

```text
不需要 MYAGENT_QDRANT_API_KEY
```

Qdrant Cloud 才需要：

```env
MYAGENT_QDRANT_URL=https://xxx.qdrant.io
MYAGENT_QDRANT_API_KEY=...
```

当前 `QdrantSettings.client_kwargs()` 已经支持这一逻辑，本阶段无需修改。

---

## 任务 5：更新 `.env.example` 和 README

重点修改：

```text
.env.example
README.md
```

README 的 Quick Start 应拆成两层。

### 最小启动

```bash
cp .env.example .env

# 只填写 LLM
myagent chat
```

明确写：

> 默认关闭 Memory 和 RAG，因此基础 Agent 不需要 Embedding 服务和 Qdrant。

### 开启 Memory / RAG

单独增加高级能力说明：

```env
MYAGENT_MEMORY_ENABLED=true
MYAGENT_RAG_ENABLED=true

EMBED_MODEL_TYPE=dashscope
EMBED_MODEL_NAME=qwen3.7-text-embedding-flash
EMBED_API_KEY=...

MYAGENT_QDRANT_URL=http://localhost:6333
```

并说明：

```text
Memory / RAG 是 Optional Capabilities，
不是运行 MyAgent Core 的前置条件。
```

同时修正 README 中容易让用户误解的描述，例如不要让：

```text
myagent chat
```

看起来天然就要求 Qdrant 和 Embedding。

---

## 任务 6：补充测试

基于当前测试结构，优先修改：

```text
tests/test_settings.py
tests/test_runtime.py
tests/test_memory.py
tests/rag/test_pipeline.py
```

至少覆盖以下场景。

### Case 1：默认配置关闭高级能力

```text
Memory.enabled == False
RAG.enabled == False
```

---

### Case 2：只有 LLM 时 Agent 可以运行

不设置：

```text
EMBED_API_KEY
MYAGENT_QDRANT_API_KEY
```

并且没有运行 Qdrant。

验证：

```text
build_agent()
→ Agent 正常创建

chat turn
→ 不调用 Embedding
→ 不访问 Qdrant
```

---

### Case 3：Memory 关闭时不访问向量能力

验证：

```text
MYAGENT_MEMORY_ENABLED=false
```

时：

```text
recall → []
observe → no-op
```

且 Fake Embedder / Fake MemoryIndex 没有被调用。

---

### Case 4：RAG 关闭时不访问向量能力

验证：

```text
MYAGENT_RAG_ENABLED=false
```

时：

```python
await rag.recall(...)
```

直接返回：

```python
[]
```

且不调用 Embedder / VectorStore。

---

### Case 5：RAG 自动召回异常时 Agent 不崩溃

模拟：

```text
EmbeddingError
或
VectorStoreError
```

验证：

```python
await rag.recall(...)
```

返回：

```python
[]
```

而不是继续抛异常。

---

### Case 6：显式 RAG 操作仍然报错

验证：

```python
await rag.retrieve(...)
```

在 Embedding / Qdrant 故障时仍然抛出对应错误。

保证：

```text
自动召回 = 可降级
显式操作 = 可诊断
```

---

# 本阶段不做的内容

为了控制复杂度，本阶段明确不做：

```text
× 不新增 Local Embedding
× 不新增 Ollama Embedding
× 不新增 FAISS / Chroma
× 不重写 BaseEmbedder
× 不重写 BaseVectorStore
× 不把 Memory 与 RAG 拆成新的插件系统
× 不重构 build_agent() 的整体装配方式
× 不扩展 Web 配置页管理 Embedding / Qdrant
```

这些都不是实现“只配置 LLM 即可使用框架”的必要条件。

---

# 最终验收标准

完成后应满足：

```text
场景 A：新用户
LLM 配置
  ↓
myagent chat
  ↓
正常运行
```

```text
场景 B：Memory / RAG 默认关闭
  ↓
不调用 Embedding
  ↓
不连接 Qdrant
```

```text
场景 C：用户主动开启 Memory / RAG
  ↓
使用现有 EmbeddingSettings
  ↓
使用现有 QdrantSettings
  ↓
获得向量 Memory / RAG 能力
```

```text
场景 D：本地 Qdrant
  ↓
只需要 http://localhost:6333
  ↓
不需要 Qdrant API Key
```

```text
场景 E：自动 RAG 召回时服务异常
  ↓
记录 warning
  ↓
跳过 RAG Context
  ↓
Agent 主流程继续
```

---

# 完成后的使用体验

```text
                    MyAgent
                       │
        ┌──────────────┴──────────────┐
        │                             │
      Core                       Optional
        │                             │
   ┌────┼─────┐                 ┌─────┴─────┐
   │    │     │                 │           │
 Chat  Tool  Session          Memory       RAG
   │  Calling  │                 │           │
   └────┬──────┘                 └─────┬─────┘
        │                              │
 Context Manager                 Embedding + Qdrant
        │
       LLM
```

核心原则：

> **MyAgent Core 只要求 LLM；Embedding 与 Qdrant 只属于 Memory / RAG 的可选增强能力。**
