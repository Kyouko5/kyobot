# ADR 0003：文档存储用 SQLite，向量存储用 Qdrant

- 状态：已接受
- 日期：2026-09-20（项目前期确定，Phase 4/5 实施）
- 关联：Phase 4（Memory Storage）、Phase 5（RAG Vector Store）；实现见 `src/myagent/config/settings.py`

## 背景

计划书 4.5 只写了「SQLite + Vector DB」，5.5 写的是「Chroma / FAISS 二选一」。存储选型会决定
Memory 与 RAG 两层的接口形状，必须在开工前定死，否则 Phase 4/5 会反复改接口。

## 决策

1. **文档与元数据存 SQLite**：文档正文、chunk 表、来源、内容 hash、时间戳、检索审计都在
   `data/myagent.db`（`MYAGENT_SQLITE_PATH`），单文件、可备份。
2. **向量存 Qdrant**：chunk embedding（以及 Phase 4 可选的 memory embedding）写入 Qdrant；
   本地默认 `http://localhost:6333`（docker 启动），云端只需 url + api key（`MYAGENT_QDRANT_*`）。
3. **两个接口彻底隔离**：Phase 4 定义 `BaseDocumentStore`（SQLite 实现），Phase 5.5 定义
   `BaseVectorStore`（Qdrant 实现）；retriever 通过 `document_id` / `chunk_id` 把向量命中还原成文本。
4. **配置只有一处来源**：`SQLiteSettings` / `QdrantSettings`（`myagent.config.settings`）；
   存储实现只接收设置对象，不直接读环境变量。

## 理由

- SQLite：标准库自带 `sqlite3`，零部署、单文件、有事务，并且能用 FTS5 做全文检索，
  为 Phase 6 的「向量 + 关键词」混合检索留了后路；chunk 元数据本质是结构化数据，
  放进向量库会失去 SQL 过滤能力。
- Qdrant：是完整的向量数据库（HNSW + payload 过滤 + 持久化 + 快照 + 官方 python client），
  docker 一行启动，形态接近生产；FAISS 只是索引文件（无服务端、无 payload 过滤、持久化自负），
  Chroma 在过滤语义与扩展性上弱于 Qdrant。选 Qdrant 也让「为什么文档库和向量库要分开」
  这个问题有明确的工程答案。
- 分开的理由：向量库擅长相似度检索、不擅长精确条件查询与事务；关系库擅长结构化查询、
  不擅长高维相似度。用 id 关联，各自做擅长的事。

## 后果

- 本机需要 Qdrant：Phase 5 提供 `docker-compose.yml`；无 docker 的环境可考虑
  `QdrantClient(path=...)` 本地模式作为测试替身（Phase 5 决定）。
- Phase 5 会新增依赖 `qdrant-client`；本阶段**不引入**，避免 Phase 0 依赖膨胀。
- 两个库之间没有跨库事务：写入顺序固定为「先 SQLite（document/chunk 行），再 Qdrant（向量）」，
  向量侧用幂等 upsert，失败时以 SQLite 为准重建（Phase 5 记录具体恢复流程）。

## 备选方案

| 方案 | 不采用的原因 |
| --- | --- |
| Chroma | 本地易用，但过滤与一致性语义弱于 Qdrant，云端形态也弱，「为什么用向量数据库」讲不清楚 |
| FAISS | 只是索引库，持久化、并发、payload 过滤都要自己实现，偏离 RAG Pipeline 的重点 |
| pgvector | 一个库搞定，但需要部署 Postgres，超出个人项目规模 |
| 向量也放 SQLite（sqlite-vec） | 部署最简，但把选型绑死在 SQLite 扩展上，放弃 Qdrant 的过滤与运维能力 |
| 文档元数据也塞进 Qdrant payload | 元数据更新、精确查询、审计都不方便 |
