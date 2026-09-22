# ADR 0009：切分参数、检索默认值与「暂不引入模型型 reranker」

- 状态：已接受
- 日期：2026-09-22
- 关联：Phase 5.3（Chunker）/ 5.5（VectorStore）/ 5.7（Reranker）；
  实验数据 [`docs/records/phase-5-rag.md`](../records/phase-5-rag.md) §6；
  实现入口 `RagSettings`（`src/myagent/config/settings.py:356`）、
  `FixedSizeChunker`（`src/myagent/rag/chunker.py:54`）、
  `src/myagent/rag/reranker.py`（`src/myagent/rag/reranker.py:29`）；
  设计说明 [`docs/rag-design.md`](../rag-design.md)

## 背景

Phase 5 有三个必须落成默认值的参数，它们都无法从原理推导出来，只能测：

1. **块大小与重叠**（PLAN 5.3）：切得太碎，一句话被切成两半，命中的上下文是残缺的；
   切得太大，一块里混进多个主题，向量被平均掉，同样预算下能塞进上下文的块也更少。
2. **检索返回几块**（PLAN 5.6）：`top_k` 直接决定 `build_context` 的体积，而 Phase 6 的上下文
   预算要为 RAG 留一份配额。
3. **要不要引入模型型 reranker**（PLAN 5.7）：cross-encoder 或托管 rerank 接口能提升排序，
   代价是每个候选一次推理，以及一个新的第三方依赖。

约束：`docs/` 下 8 篇设计文档（约 12 万字符）当作语料，8 个问题各只有一篇文档能回答；
同一套问题、同一个嵌入模型，只改被测量的参数。判定指标是 PLAN 验收标准里的
`hit@5`（目标文档出现在前 5 块里）、平均命中位次、平均块长与检索延迟。

## 决策

1. **块大小 800 字符、重叠 120 字符**，作为 `MYAGENT_RAG_CHUNK_SIZE` /
   `MYAGENT_RAG_CHUNK_OVERLAP` 的默认值（`src/myagent/config/settings.py:138`、
   `src/myagent/config/settings.py:139`）。重叠固定为块大小的 15%。
2. **`top_k = 5`**，作为 `MYAGENT_RAG_TOP_K` 的默认值（`src/myagent/config/settings.py:140`）。
3. **不引入模型型 reranker**：V1 只有 `IdentityReranker`（默认）与 `ScoreReranker`
   （`src/myagent/rag/reranker.py:39`、`:49`），两者都不依赖第三方服务。
   引入条件是 PLAN 5.7 写明的「`hit@3` 的提升 > 延迟增量」，**留待 Phase 8 用更大的标注集判定**。
4. **`pypdf` 成为运行依赖**（`pyproject.toml` 的 `dependencies`）：`.pdf` 是 PLAN 5.2 的
   三种格式之一，而 PDF 的文本层抽取是纯增量能力，标准库没有替代品。
   范围限定在 `src/myagent/rag/loader.py:36` 一处 import。
5. **归一化默认开启**（`DashScopeEmbedder` / `OpenAIEmbedder` 的 `normalize=True`，
   `src/myagent/rag/embedder.py:207`、`:231`）：cosine 等价于点积，向量模长不再参与排序。

## 理由

- **三档块大小的 `hit@5` 完全一样（8/8），差异在位次上**：400 字符的平均命中位次是 1.00，
  800 是 1.38，1200 是 1.62。也就是说，块越大，正确答案越容易被别的块挤到后面——
  一个块装进了多个主题，向量的方向被平均掉了。
- **但 400 的代价是向量数翻倍**：同一批文档切成 421 / 216 / 144 块。索引规模、写入成本与
  查询时的 `top_k` 候选量都跟着翻倍，而位次只改善 0.38；检索延迟三档相同（~95 ms，
  由 query 的嵌入调用主导，与块大小无关）。
- **800 是「位次」与「规模」的折中**：它在 8/8 的前提下把向量数压到 400 的一半，
  同时比 1200 更聚焦。这个取舍在 8 篇文档 / 8 个问题上成立，标注集变大约 1 个数量级后
  应当复算——**结论的量级比结论本身更值得记住**。
- **`top_k = 5` 与记忆的 `MYAGENT_MEMORY_TOP_K` 一致**：两类上下文的体积可比，
  Phase 6 的预算表（PLAN 6.2：RAG 35%）才有意义。
- **重排要有第二个信号才有意义**：实验里 `IdentityReranker` 与 `ScoreReranker` 的
  `hit@5` 与平均位次**完全一致**（8/8、1.38），因为向量库本来就按 score 返回；
  重排自身 0.03–0.05 ms，而端到端延迟 ~100 ms 全花在 query 嵌入上。
  用同一种分数再排一次，得到的是同一个顺序。
- **`pypdf` 是必要依赖而不是便利依赖**：`.md` / `.txt` 用标准库足够，
  `.pdf` 的文本层抽取没有标准库方案；不做 OCR 是 PLAN 5.2 明确写的 non-goal，
  所以依赖范围被限制在「读文本层」，`UnreadableDocumentError` 覆盖解析失败。
- **归一化让契约更简单**：开启之后 `VectorStore` 的 cosine 与 `openai` 兼容端点的
  点积语义一致，score 落在可解释的区间里，Phase 8 的比较不用担心模长漂移。

## 后果

- **块大小是可配置的**，Phase 8 复算实验只需要改 `MYAGENT_RAG_CHUNK_SIZE`，
  不需要动代码（`RagSettings` 的 docstring 明确写了这一点）。
- **8 篇 / 8 问的样本很小**：所有三档都拿到 8/8，判别力全部落在平均位次上。
  因此 ADR 的措辞是「800 在现有数据上是折中」，而不是「800 最优」。
- **改块大小需要重新摄取**：块的 id 是 `document_id:index`，切法一变，块号就全变了。
  旧向量必须重建（`myagent docs delete` 之后重新 `ingest`）；没有增量迁移。
- **不引入 reranker 意味着 `top_k` 就是最终精度**：没有第二道筛选，块级噪声直接进上下文。
  这是本阶段有意接受的成本，Phase 8 的 `hit@3` 数据是它的复核点。
- **归一化是提供方默认行为**：`OpenAICompatEmbedder` 本身默认关闭，
  `DashScopeEmbedder` / `OpenAIEmbedder` 默认开启；Phase 4 的记忆沿用后者，
  所以记忆与文档的向量处在同一个归一化约定下，余弦可以直接比较。
- **`EMBED_DIM` 被写进 `.env`**：一次摄取的副作用是修改配置文件（ADR-0005 第 7 条）。
  好处是「一次探测、后续一致」；代价是测试必须隔离这一行
  （`tests/conftest.py:35`）。

## 备选方案

| 方案 | 不采用的原因 |
| --- | --- |
| 块大小固定 400（位次最好） | 向量数与写入成本翻倍（421 vs 216），位次只改善 0.38；在更大的标注集上才有理由 |
| 块大小 1200（块数最少） | 平均命中位次退到 1.62，一个块混多个主题；上下文里单块噪声更大 |
| 不设重叠（`overlap=0`） | 跨切分点的句子会从两侧都检索不到，这是最容易被读者发现的失败模式 |
| 按 token 而不是字符切 | token 需要真实分词器（Phase 6 才有 `count_tokens` 的真实值）；字符预算对中英混排已经够稳 |
| 引入 `RecursiveChunker`（`\n\n` → `\n` → `。` → 字符的显式层级） | 当前的贪心策略已经实现了「段落优先、句子次之」的效果；层级化是 §7.1 的可选方向 |
| 引入 cross-encoder / 托管 rerank 接口 | 实验里没有第二个信号可依据，收益未测；PLAN 5.7 要求先有 `hit@3` 的证据 |
| 用 BM25 做第二路召回（混合检索） | PLAN §7.1 的可选方向，且需要标注集来定融合权重；本阶段只保留 `keyword()` 作为离线对照 |
| 用 `sqlite-vec` 取代 Qdrant（少一个依赖） | 与 ADR-0003 冲突，且放弃 payload 过滤与运维形态 |
| 用 `pymupdf` / `pdfminer` 替代 `pypdf` | 两者能力更强但体积与许可成本更高；本阶段只需要文本层 |
