# RAG 设计：从文件到带引用的答案（Phase 5）

- 状态：已实现（Phase 5）
- 关联文档：[`docs/agent-loop.md`](./agent-loop.md)（上游的上下文与工具机制）、
  [`docs/decision-records/0009-chunking-and-retrieval.md`](./decision-records/0009-chunking-and-retrieval.md)（决策）、
  [`docs/records/phase-5-rag.md`](./records/phase-5-rag.md)（实验与质量门）
- 代码入口：`src/myagent/rag/`（9 个文件 1222 行）；装配点 `src/myagent/runtime.py:151`（`build_rag`）

## 0. 一句话

上游 nanobot **没有检索**：包内搜不到 embedding、向量库或相似度检索的任何实现，
模型能看到的只有 system prompt、会话历史与记忆文件。所以 Phase 5 是**纯增量能力**，
不是对既有代码的改写；它要回答的问题是**「文件怎么变成模型能引用的答案」**。

答案是一条两段的流水线：**摄取**（文件 → 归一化文本 → 带重叠的块 → 向量 → 向量库 + SQLite）
与**检索**（问题 → 向量 → 最近邻 → 回到 SQLite 取原文 → 重排 → 带 `[文档id#块号]` 的上下文）。
两段都是**幂等**的：同一个文件摄取两次不会变成两份数据（PLAN 5.1）。

```text
摄取：Document → Loader → Chunker → Embedder → VectorStore（+ SQLite 存原文）
检索：Query    → Embedder → VectorStore.search → (Reranker) → RetrievedChunk[] → build_context
```

## 1. 全景图：谁在什么时候被调用

```text
myagent ingest a.pdf b.md
   └── src/myagent/cli.py:218 _rag_ingest → RagPipeline.ingest（src/myagent/rag/pipeline.py:171）
         ① load_document            （src/myagent/rag/loader.py:195）按后缀选 Loader
         ② FixedSizeChunker.split   （src/myagent/rag/chunker.py:86）  段落 → 句子 → 字符
         ③ BaseEmbedder.embed       （src/myagent/rag/embedder.py:167）批量 16 + 重试 3 次
         ④ VectorStore.ensure_collection（src/myagent/rag/vectorstore.py:115）幂等 + 维度检查
         ⑤ SQLiteDocumentStore.put_document / replace_chunks（src/myagent/rag/store.py:98、:144）
         ⑥ QdrantVectorStore.upsert （src/myagent/rag/vectorstore.py:143）

myagent search "问题" -k 5
   └── src/myagent/cli.py:240 _rag_search → RagPipeline.retrieve（src/myagent/rag/pipeline.py:240）
         → VectorRetriever.retrieve（src/myagent/rag/retriever.py:84）
            embed(query) → vectorstore.search → 回 SQLite 解析命中
         → BaseReranker.rerank（src/myagent/rag/reranker.py:29）
         → RagPipeline.build_context（src/myagent/rag/pipeline.py:270）
```

四个契约都是 Phase 3 立好的（`BaseLoader` 是 Phase 5 新增的第五个）：
`BaseLoader` / `BaseChunker` / `BaseEmbedder` / `BaseVectorStore` / `BaseRetriever` / `BaseReranker`
（`src/myagent/rag/loader.py:71`、`src/myagent/rag/chunker.py:46`、
`src/myagent/rag/embedder.py:74`、`src/myagent/rag/vectorstore.py:63`、
`src/myagent/rag/retriever.py:43`、`src/myagent/rag/reranker.py:29`）。**只有 `myagent.rag` 的两个模块 import provider SDK**
（`vectorstore.py` 的 `qdrant_client`、`embedder.py` 的 `openai`），其余代码只认契约——
这正是 `tests/rag/` 能在没有服务器、没有密钥的情况下跑完整条流水线的原因。

## 2. 数据模型（PLAN 5.1）

四类纯数据（`src/myagent/rag/types.py`），没有一处 import SDK：

| 类型 | 字段 | 位置 |
| --- | --- | --- |
| `Document` | `id` / `source` / `title` / `text` / `metadata` | `src/myagent/rag/types.py:108` |
| `Chunk` | `id` / `document_id` / `index` / `text` / `metadata` | `src/myagent/rag/types.py:119` |
| `ScoredPoint` | `id` / `score` / `payload`（向量库的原始命中） | `src/myagent/rag/types.py:130` |
| `RetrievedChunk` | `chunk` / `score` / `document`（能直接拼引用） | `src/myagent/rag/types.py:139` |

`Document.id` 是**内容寻址**的：归一化全文的 sha256 前 16 位（`src/myagent/rag/types.py:54` 的
`content_id`，长度常量 `ID_CHARS` 在 `src/myagent/rag/types.py:41`）。这条性质有三层后果：

1. **幂等**：同一个文件第二次摄取，SQLite 按 `sha256 UNIQUE` 找到同一行（`src/myagent/rag/store.py:98`）；
2. **改名不算新文档**：`source` 只是属性，身份来自内容；
3. **改一个字就是新文档**：哈希变了，`put_document` 插入新行。

`Chunk.id` 是 `f"{document_id}:{index}"`（`src/myagent/rag/types.py:71` 的 `chunk_id`），
它同时是引用里的块号、SQLite 的主键、以及 Qdrant 点 id 的**派生来源**（见 §5）。

### 2.1 SQLite：原文与元数据（`src/myagent/rag/store.py`）

```text
documents(id TEXT PK, source TEXT, title TEXT, sha256 TEXT UNIQUE, created_at TEXT, metadata TEXT)
chunks(id TEXT PK, document_id → documents(id) ON DELETE CASCADE, idx INTEGER, text TEXT,
       page INTEGER, metadata TEXT)                      索引：chunks(document_id)
```

与 Phase 4 的记忆**共用 `data/myagent.db` 文件、不共用表**（`src/myagent/rag/store.py:45`）：
文档与记忆互相不认识，把它们放在同一个文件里只是「少一个要备份的东西」，
而表才是隔离手段——这也是 ADR-0008 选择让 Qdrant collection 分开的同一条理由。

## 3. Loader：三种格式，一个归一化（PLAN 5.2）

```python
class BaseLoader(Protocol):      # src/myagent/rag/loader.py:71
    def supports(self, path: Path) -> bool: ...
    def load(self, path: Path) -> Document: ...
```

| 实现 | 后缀 | 依赖 | 额外产出 |
| --- | --- | --- | --- |
| `TextLoader` | `.txt` | 标准库 | — |
| `MarkdownLoader` | `.md` / `.markdown` | 标准库 | `metadata["headings"]`（ATX 标题的层级/文本/偏移） |
| `PdfLoader` | `.pdf` | `pypdf`（ADR-0009） | `metadata["page_spans"]`（每页在归一化文本里的区间） |

`load_document`（`src/myagent/rag/loader.py:195`）按注册顺序取**第一个认领该后缀**的 loader；
没有认领者就抛 `UnsupportedFormatError`，错误消息里带上当前支持的后缀
（`src/myagent/rag/loader.py:217` 的 `supported_suffixes`）。新增格式是**加一个类**，
不是改一处 `if` ——Phase 3 的扩展点约定（ADR-0007）照旧。

两个不那么显然的设计点：

- **归一化只有一处**：`normalize_text`（`src/myagent/rag/loader.py:83`）把 `\r\n`/`\r` 统一成 `\n`、
  去掉行尾空白、把连续空行压成一个空行。内容哈希算的是**归一化之后**的文本，
  所以「换行符不同但内容相同」的文件不会变成第二篇文档。
- **页码是 loader 的责任**：chunker 不应该知道 PDF 是什么，所以 PDF loader 记下
  `page_spans`（每页起止偏移），`page_at`（`src/myagent/rag/types.py:76`）再把 chunk 的
  `char_span` 翻译成页码。Markdown 同理，用 `heading_at`（`src/myagent/rag/types.py:91`）
  给出「这一块属于哪一节」。**没有文字层的 PDF 会加载成空文档**：摄取照样存它
  （`myagent docs list` 里能看到 0 chunk），而不是凭空编内容。

## 4. Chunker：一个字符预算 + 边界偏好（PLAN 5.3）

`FixedSizeChunker(size=800, overlap=120)`（`src/myagent/rag/chunker.py:54`）的切法：

```text
段落（\n\n 分隔）
  ├── 段落能塞进剩余预算 → 整段放进去（绝不切碎一个完整的段落）
  └── 段落太长 → 退回句子切（。！？；;!? 或句号+空格或换行）
        └── 句子还是太长 → 按字符步进（size-overlap 的步长）
```

重叠是**给切分错误买的保险**：切分点在字符上，语义边界在句子/段落上，两者不可能对齐，
让相邻两块共享 `overlap` 个字符，跨界的那句话就能从两侧都被检索到。

每个 chunk 的 `metadata` 有四个字段（`src/myagent/rag/chunker.py:93` 的 `_chunk`）：
`char_span`（在 `Document.text` 里的半开区间）、`page`（由 `page_at` 翻译）、
`heading`（由 `heading_at` 翻译）、`token_estimate`（`src/myagent/tokens.py` 的估算函数，
Phase 6 的预算直接复用同一个函数）。

参数不是拍脑袋定的：ADR-0009 用 400 / 800 / 1200 三档跑了同一批标注问题，
数据在 `docs/records/phase-5-rag.md` §6.1。

## 5. Embedder 与 VectorStore：维度是第一个要处理的问题（PLAN 5.4 / 5.5）

### 5.1 维度探测：先观察，再记住，最后才建集合

`EMBED_DIM` 留空时，第一次摄取**用一次真实调用观察 `len(vector)`**，然后：

1. 写回 `.env`（`src/myagent/config/env.py:124` 的 `remember_env`，逐行替换 `EMBED_DIM=`）；
2. 写进报告，CLI 打印 `embedding dim=1024 (probed, written to .env)`（`src/myagent/cli.py:235`）；
3. 用这个数字建 collection（`src/myagent/rag/pipeline.py:187`）。

为什么值得为此写文件：**collection 是为一个维度建的**。向量库不会「自动适配」，
写错的表现不是报错，而是「检索永远返回空」——一个很难倒查的失败。
所以 `ensure_collection`（`src/myagent/rag/vectorstore.py:115`）是幂等 + **带维度检查**的：
已存在且维度一致就跳过；已存在但维度不一致就报出两个数字
（`EMBED_DIM is 1024 but the embedding model '…' returned 768-dimensional vectors`，
`src/myagent/rag/pipeline.py:220` 与 `src/myagent/rag/vectorstore.py:115` 各拦一道，
前者拦「配置与模型不符」，后者拦「模型与已有集合不符」）。

`remember_env` 直接写 `os.environ`（让本次运行立刻看到新值）**再**改 `.env`；
这行写入是测试里唯一需要额外隔离的东西（`tests/conftest.py:35` 的 `isolated_probed_dim`）。

### 5.2 传输层：批量、重试、归一化

`OpenAICompatEmbedder`（`src/myagent/rag/embedder.py:109`）只管三件事：

- **批量**：一次最多 `EMBED_BATCH_SIZE`（默认 16）条，分批由 `_batches`
  （`src/myagent/rag/embedder.py:263`）切；
- **重试**：3 次指数退避（`src/myagent/rag/embedder.py:182`），失败翻译成 `EmbeddingError`；
- **归一化**：`DashScopeEmbedder` / `OpenAIEmbedder` 默认 `normalize=True`
  （`src/myagent/rag/embedder.py:207`、`:231`），用 `normalize_vector`
  （`src/myagent/rag/embedder.py:91`）把向量缩放到单位长度，于是余弦相似度等价于点积。
  传输层默认**关闭**归一化，这样 Phase 4 的记忆行为与它的向量 fixture 一字未变。

### 5.3 Qdrant：payload 里放什么

```python
point_id(chunk.id) = uuid5(NAMESPACE_DNS, chunk.id)      # src/myagent/rag/vectorstore.py:209
payload = {chunk_id, document_id, page, idx}             # src/myagent/rag/vectorstore.py:220
```

**点 id 是派生的，逻辑 id 在 payload 里。** 这一条是 Phase 4 用真实服务换来的教训
（`docs/records/phase-4-memory.md` §8.2）：Qdrant 会把 `id` 归一化成带连字符的 UUID，
而 `3f2a…:12` 这样的块 id 根本不是 UUID。如果拿点 id 当逻辑 id 用，回查 SQLite 就查不到，
命中的向量会被整批丢弃、检索退回兜底。派生 UUID 是确定性的，所以**重复摄取同一个文件，
点 id 不变，点数量也不变**（幂等验收的依据）。

payload 四个字段各有用途（PLAN 5.5）：`document_id` 支持按文档过滤（多篇论文对比，
Phase 7 要用），`page` 让引用能指回原文位置，`idx` 用于同一文档内部排序，
`chunk_id` 是回到 SQLite 的钥匙。过滤值支持标量与列表两种形状
（`src/myagent/rag/vectorstore.py:288` 的 `_match`：列表 → `MatchAny`）。

## 6. Retriever：命中之后回到原文（PLAN 5.6）

`VectorRetriever.retrieve`（`src/myagent/rag/retriever.py:84`）只有三步，
但有一条规则值得单独说：**命中必须在 SQLite 里解析成功才作数**。

```text
query ─ embed ─► vector ─ search ─► hits（payload 里的 chunk_id）
                                     │ 查 SQLite：chunk → document
                                     │ 查不到（陈旧点、别的 collection 的遗留）→ 记 warning 并跳过
                                     ▼
                              RetrievedChunk（带 score 与完整 Chunk）
```

这样「向量库里有、记录库里没有」的内容永远不会进 prompt：模型看到的每一段文本
都能追溯到一份可查的记录。`score` 与 `chunk.id` 原样保留，因为 Phase 8 的 `hit@k`
就是拿这两个字段算的。

两条边界：空问题与非正 `top_k` 直接返回空列表（不向 provider 发一个「嵌入空字符串」的请求）；
`keyword()`（`src/myagent/rag/retriever.py:100`）做子串匹配扫记录库——**不需要任何外部服务**，
是实验里的离线对照，也是「没有 key 时还能验证流程」的地板。

失败是**抛出而不是吞掉**（`EmbeddingError` / `VectorStoreError`）：记忆可以降级到关键词，
因为它总有一条已经落盘的记录可用；RAG 没有记录可退，所以 CLI 直接给出一行可读的错误。

## 7. Reranker：先留接口，不引入模型（PLAN 5.7）

| 实现 | 行为 | 位置 |
| --- | --- | --- |
| `IdentityReranker` | 原样截断（默认） | `src/myagent/rag/reranker.py:39` |
| `ScoreReranker` | 按已知 score 排序后截断，并列时按块 id 决出全序 | `src/myagent/rag/reranker.py:49` |

**不引入 cross-encoder / 托管 rerank 接口**，理由是实验数据而不是喜好：候选池 10 → top 5
时，两个重排器的 `hit@5` 与平均位次完全一致（8/8、1.38），而重排自身耗时 0.03–0.05 ms、
端到端延迟由 query 嵌入调用主导（~100 ms）。用同一种信号重排一次不会改变排序——
**重排要有第二个信号才值得**（交叉编码器或 BM25），这留给 Phase 8 用更大的标注集判定
（PLAN 5.7 的引入条件写进 ADR-0009）。

## 8. Pipeline 与 CLI（PLAN 5.8）

`RagPipeline`（`src/myagent/rag/pipeline.py:130`）是唯一的门面，四个方法：

| 方法 | 语义 |
| --- | --- |
| `ingest(paths)` | 加载 → 切分 → 嵌入 → 建集合 → 写 SQLite + Qdrant；返回 `IngestReport` |
| `retrieve(query, top_k, document_ids=…)` | 检索 + 重排 |
| `build_context(chunks)` | 拼成带 `[document_id#index]` 引用的上下文 |
| `documents()` / `delete(id)` | 列出 / 删除（先删向量，再删行，chunks 级联） |

```bash
myagent ingest docs/*.md          # 摄取（幂等；首次会探测并写回 EMBED_DIM）
myagent search "切分参数怎么定" -k 5
myagent search "重排" -d 44603fec319acac5   # 限定某一篇（可重复）
myagent docs list                 # 文档 id / 块数 / 页数 / 时间 / 来源
myagent docs delete <document_id> # 删文档：向量 + 行（chunks 级联）
```

`build_context` 的形状是刻意无聊的（`src/myagent/rag/pipeline.py:270`）：

```text
[a8361ccbbb4d80a2#8] 开发规范 (no page)
<该块的正文>

[44603fec319acac5#3] Memory 设计：四层记忆、写入策略、检索与巩固（Phase 4） (no page)
<该块的正文>
```

引用包含三样东西：**稳定的文档 id**（能在 `docs list` 里查到）、**块号**（第几段）、
**人肉定位线索**（页码或标题）。形状稳定有两个好处：Phase 6 能算它占多少预算，
不同实验的测量结果互相可比（`docs/records/phase-5-rag.md` §6.3 就用它算「答案词进入上下文的比例」）。

装配在 `build_rag`（`src/myagent/runtime.py:151`）：和 `build_memory` 并列，
因为 `ingest` / `search` 需要 RAG 而不需要聊天模型，且两个入口必须读同一个 SQLite 文件、
同一个 Qdrant collection、同一份嵌入配置。`embedder` 可注入，实验与测试都靠这个口子。

## 9. 配置项

| 变量 | 默认 | 作用 |
| --- | --- | --- |
| `EMBED_MODEL_TYPE` / `EMBED_MODEL_NAME` / `EMBED_API_KEY` / `EMBED_BASE_URL` | dashscope / `qwen3.7-text-embedding-flash` | 嵌入服务（ADR-0005） |
| `EMBED_DIM` | 空 | 留空 = 首次摄取探测并写回（§5.1） |
| `EMBED_BATCH_SIZE` | 16 | 单次请求最多几条文本（`src/myagent/config/settings.py:50`） |
| `MYAGENT_RAG_CHUNK_SIZE` | 800 | 块大小（字符） |
| `MYAGENT_RAG_CHUNK_OVERLAP` | 120 | 相邻块的重叠（字符） |
| `MYAGENT_RAG_TOP_K` | 5 | 检索返回几块 |
| `MYAGENT_QDRANT_COLLECTION` | `myagent_documents` | 文档块集合（与记忆集合分开，ADR-0008） |

`RagSettings`（`src/myagent/config/settings.py:356`）在构造时就拒绝非法组合：
`chunk_overlap >= chunk_size`、非正的 `chunk_size` / `top_k` 都直接 `ValueError`——
配置错误应该在启动时炸，而不是在检索结果变差时才被发现。

## 10. 与上游 nanobot 的对照

| 关注点 | 上游 nanobot | MyAgent Phase 5 |
| --- | --- | --- |
| 检索能力 | **无**（无 embedding、无向量库、无相似度检索） | Loader → Chunker → Embedder → Qdrant → Retriever |
| 外部资料的进入方式 | 工具读文件（`read_file` / `search_local`）后塞进上下文 | 预先摄取成知识库，按相似度取回 |
| 上下文里的证据 | 工具输出的原文，没有稳定标识 | `[document_id#index]` 引用，可回跳 |
| 切分 | 无（文件整篇读） | 800 字符 + 120 重叠，段落/句子优先 |
| 幂等 | 不适用 | 内容寻址 + `sha256 UNIQUE` + 派生点 id |
| 失败语义 | 工具错误回给模型 | `EmbeddingError` / `VectorStoreError` 抛给 CLI（RAG 无兜底记录可退） |

上游的 `search_local` 仍是受控桩（`src/myagent/tools/builtin/search_local.py:60`），
Phase 7 的真实论文检索会走本阶段的 `RagPipeline` **或者**把它封装成一个工具——
这正是「Loop 不直接负责 RAG」（`docs/design.md` §6.1）留给下一阶段的选择空间。

## 11. 与 Phase 4 记忆的边界

两者结构相似（SQLite 记录 + Qdrant 向量 + 一段检索逻辑），但**不是同一个东西**：

| | Memory（Phase 4） | RAG（Phase 5） |
| --- | --- | --- |
| 存什么 | 被提炼的结论（偏好、事实、事件） | 原始文档与它的块 |
| 谁写 | 每轮对话自动抽取 + 策略 | 显式 `myagent ingest` |
| 检索失败 | 降级为关键词，`degraded=True` | 抛错（没有兜底记录可退） |
| 衰减 | Episodic 半衰期 30 天 | 无（文档不会过期） |
| Qdrant collection | `myagent_memories` | `myagent_documents` |

一条代码层面的联系：`src/myagent/memory/embedder.py` 在 Phase 5 **被删掉并搬到**
`src/myagent/rag/embedder.py`。嵌入能力属于 RAG，记忆只是使用者；
依赖方向因此是 `memory → rag`（`src/myagent/runtime.py:140` 用 `build_embedder`），
而不是两个模块各持一份传输层。行为一字未改，只是路径变了（Phase 4 的测试只改了 import）。

## 12. 已知限制与明确不做

| 限制 / non-goal | 说明 |
| --- | --- |
| 不做 OCR / 扫描件 / 表格结构化 / 公式抽取 | PLAN 5.2 的 non-goal；没有文字层的 PDF 存成空文档 |
| Setext 标题（`===` 下划线式）与代码块内的 `#` 不识别 | `MarkdownLoader` 只认 ATX 标题，避免引入完整的 Markdown 解析器 |
| 切分器是贪心定长，不是递归语义切分 | `RecursiveChunker` 是 §7.1 的可选方向；先有可测量的一版 |
| 没有混合检索（BM25 + 向量 + 融合） | PLAN §7.1 的可选方向；本阶段只有 `keyword()` 这个离线对照 |
| 没有模型型 reranker | ADR-0009：等 Phase 8 的 `hit@3` 数据 |
| 没有增量更新 | 改一个字符 = 新文档（内容寻址的代价）；旧文档要显式 `docs delete` |
| `MYAGENT_RAG_ENABLED` 开关 | 不在这里：它是 Context 的开关（PLAN 6.5），Phase 6 接进 Loop 时才需要 |
| 块级别的权限 / 多租户 | 单人本地知识库，不做 |
