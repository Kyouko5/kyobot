# Phase 5 工作记录：RAG 系统建设（Loader → Chunker → Embedding → Store → Retriever）

- 日期：2026-09-22
- 状态：已完成
- 关联提交：本阶段的实现提交 `feat(phase5): implement the end-to-end RAG pipeline`、
  实验脚本提交 `chore(scripts): add the phase 5 RAG experiment harness`，
  本文档与 `docs/rag-design.md`、ADR-0009 在同一次文档提交里
- 关联文档：[`docs/rag-design.md`](../rag-design.md)（设计）、
  [`docs/decision-records/0009-chunking-and-retrieval.md`](../decision-records/0009-chunking-and-retrieval.md)（决策）、
  [`docs/design.md`](../design.md) §3.11（模块职责）、
  [`docs/decision-records/0005-embedding-provider.md`](../decision-records/0005-embedding-provider.md)（嵌入提供方）

## 1. 阶段目标

上游 nanobot **没有检索能力**：包内搜不到 embedding、向量库或相似度检索的任何实现
（`docs/architecture.md` 的模块地图里也没有这一层）。所以 Phase 5 不是改写，而是**纯增量**：
把「文件 → 可引用片段 → 带引用的答案」做成一条端到端、可插拔、可测量的流水线。

要回答的四个问题：

| 问题 | 本阶段的答案 |
| --- | --- |
| 文件怎么进来 | 三种 Loader（`.txt` / `.md` / `.pdf`）+ 一处归一化，页码与标题层级由 loader 记 |
| 长文怎么切 | `FixedSizeChunker(800, 120)`：段落优先、句子次之、字符兜底 + 重叠；参数由实验定（§6.1） |
| 向量怎么存、怎么查 | 记录（原文 + 元数据）在 SQLite、向量在 Qdrant；命中必须回 SQLite 解析成功才作数 |
| 答案怎么引用 | `[document_id#index]` + 标题 + 页码，形状稳定到能算预算、能横向比较 |

## 2. Baseline（Phase 4 结束时，`docs/records/phase-4-memory.md`）

| 项 | Phase 4 结束 | Phase 5 结束 |
| --- | --- | --- |
| 测试 | 442 项 | 591 项（+149；其中 `tests/rag/` 108 项） |
| 覆盖率 | 2785 stmts / 662 branches，100% | 3550 stmts / 828 branches，100% |
| `src/myagent/` | 50 个文件 / 6386 行 | 53 个文件 / 8359 行（`rag/` 9 个文件 1998 行） |
| RAG | `rag/` 只有契约与数据类型（Phase 3 立的三个 Protocol） | 9 个文件的端到端实现 + 三个新契约 |
| 检索能力 | 无（`search_local` 是受控桩） | `myagent ingest` / `search` / `docs list` / `docs delete` |
| 嵌入传输层 | `src/myagent/memory/embedder.py`（Phase 4 的临时位置） | 搬到 `src/myagent/rag/embedder.py`，依赖方向 `memory → rag` |
| 运行依赖 | dotenv / openai / qdrant-client | + `pypdf>=5.0`（ADR-0009） |
| 文档锚点 | 865 个（16 篇） | 见 §7 的质量门输出 |

## 3. 方案设计

| 决策 | 依据 | 位置 |
| --- | --- | --- |
| 两段流水线：`ingest` 幂等、`retrieve` 无状态 | PLAN 5.0 | `src/myagent/rag/pipeline.py:171`、`:211` |
| 文档身份 = 归一化全文的 sha256 前 16 位 | PLAN 5.1 | `src/myagent/rag/types.py:54` |
| 记录在 SQLite（`documents` + `chunks`），向量在 Qdrant | ADR-0003 | `src/myagent/rag/store.py:45`、`src/myagent/rag/vectorstore.py:85` |
| 归一化只有一处（`\r\n` / 行尾空白 / 连续空行） | 内容哈希必须稳定 | `src/myagent/rag/loader.py:83` |
| 页码与标题是 loader 的责任（`page_spans` / `headings`） | chunker 不该知道 PDF 是什么 | `src/myagent/rag/loader.py:158`、`src/myagent/rag/types.py:76`、`:91` |
| 切分：段落 → 句子 → 字符 + 重叠 | PLAN 5.3 | `src/myagent/rag/chunker.py:86`、`:109` |
| 默认 800 字符 / 120 重叠 / `top_k=5` | 本阶段实验（§6.1） | `src/myagent/config/settings.py:138`、ADR-0009 |
| 维度：`EMBED_DIM` 留空则探测一次并写回 `.env` | PLAN 5.4 | `src/myagent/rag/pipeline.py:230`、`src/myagent/config/env.py:124` |
| 维度两处校验（配置 vs 模型、模型 vs 已有集合） | 静默失败最难查 | `src/myagent/rag/pipeline.py:220`、`src/myagent/rag/vectorstore.py:115` |
| 归一化默认开启（cosine ≡ 点积） | PLAN 5.4 | `src/myagent/rag/embedder.py:207`、`:231` |
| 点 id = uuid5(块 id)，逻辑 id 进 payload | Phase 4 的教训（§8.2） | `src/myagent/rag/vectorstore.py:209`、`:220` |
| 命中回 SQLite 解析，解析不到就跳过 | 「向量有、记录没有」的内容永不进 prompt | `src/myagent/rag/retriever.py:141` |
| 检索失败**抛出**（不像记忆那样降级） | RAG 没有可退的兜底记录 | `src/myagent/rag/retriever.py:123` |
| reranker 只给两个无依赖实现，不引模型 | 本阶段实验（§6.2） | `src/myagent/rag/reranker.py:39`、`:49` |
| `embedder` 传输层从 memory 搬到 rag | 嵌入能力属于 RAG | `src/myagent/rag/embedder.py:109`、`src/myagent/runtime.py:140` |
| 装配单独提供 `build_rag()` | CLI 要「只要 RAG、不要聊天模型」 | `src/myagent/runtime.py:151` |

## 4. 实现

| 文件 | 行数 | 职责 |
| --- | ---: | --- |
| `src/myagent/rag/types.py` | 144 | `Document` / `Chunk` / `ScoredPoint` / `RetrievedChunk` / `Filter` + `content_id` / `chunk_id` / `page_at` / `heading_at` |
| `src/myagent/rag/loader.py` | 269 | `BaseLoader` + `TextLoader` / `MarkdownLoader` / `PdfLoader`，`normalize_text`，`load_document` |
| `src/myagent/rag/chunker.py` | 179 | `BaseChunker` + `FixedSizeChunker`（段落 → 句子 → 字符 + 重叠） |
| `src/myagent/rag/embedder.py` | 265 | `BaseEmbedder` + `OpenAICompatEmbedder`（批量 16、重试 3、错误翻译）+ `DashScopeEmbedder` / `OpenAIEmbedder` + `normalize_vector` |
| `src/myagent/rag/vectorstore.py` | 292 | `BaseVectorStore` + `QdrantVectorStore`（payload、派生点 id、幂等建集合、维度检查、错误翻译） |
| `src/myagent/rag/store.py` | 289 | `SQLiteDocumentStore`：`documents` / `chunks`、`sha256 UNIQUE`、级联删除 |
| `src/myagent/rag/retriever.py` | 154 | `BaseRetriever` + `VectorRetriever`（embed → search → 回 SQLite 解析）+ `keyword()` 离线对照 |
| `src/myagent/rag/reranker.py` | 63 | `BaseReranker` + `IdentityReranker` / `ScoreReranker` |
| `src/myagent/rag/pipeline.py` | 245 | `RagPipeline`（`ingest` / `retrieve` / `build_context` / `documents` / `delete`）+ `IngestReport` + `citation` |
| `src/myagent/rag/__init__.py` | 98 | 全部公开符号的 re-export（`from myagent.rag import ...` 一个入口） |
| `src/myagent/config/env.py` | +47 | 新增 `remember_env`：把探测到的维度写回 `.env`（逐行替换/追加） |
| `src/myagent/config/settings.py` | +92 | `EMBED_BATCH_SIZE`、`RagSettings`（`MYAGENT_RAG_*`）、`Settings.rag` |
| `src/myagent/runtime.py` | +36 | `build_rag()`；`build_memory` 改用 `build_embedder` |
| `src/myagent/cli.py` | +134 | `myagent ingest|search|docs` 四个入口与 `_hit_line` |
| `src/myagent/memory/embedder.py` | −133 | 删除（搬到 `rag/embedder.py`），`memory/__init__.py` / `retriever.py` / `runtime.py` 改 import |

测试（`tests/rag/`，108 项，全部离线）：

| 文件 | 项数 | 覆盖 |
| --- | ---: | --- |
| `tests/rag/test_loader.py` | 18 | 三种格式、协议自检、归一化、内容寻址、PDF 逐页、损坏 PDF、缺失文件、未知后缀 |
| `tests/rag/test_chunker.py` | 10 | 段落/句子/字符三条路径、重叠、`char_span`、页码与标题、`token_estimate`、上限 |
| `tests/rag/test_store.py` | 11 | upsert、`sha256 UNIQUE`、级联删除、读取顺序、幂等行数 |
| `tests/rag/test_embedder.py` | 14 | 批量切分、重试与退避、失败翻译、维度记忆、归一化、两个 provider 的端点 |
| `tests/rag/test_vectorstore.py` | 19 | payload、派生点 id、维度冲突、过滤（标量/列表）、错误翻译、`count` |
| `tests/rag/test_retriever.py` | 12 | 解析命中、跳过陈旧命中、空 query、`top_k<=0`、过滤、关键词兜底 |
| `tests/rag/test_reranker.py` | 5 | 两个实现的截断与排序、并列时的全序 |
| `tests/rag/test_pipeline.py` | 19 | 端到端 ingest/retrieve/build_context/delete、维度探测与冲突、空文档 |

## 5. 实验方法

- **语料**：本仓库 `docs/` 下 8 篇设计文档（`architecture` / `agent-loop` / `tool-system` /
  `context` / `memory` / `memory-design` / `design` / `development`），约 12 万字符，
  中英混排、含标题与代码块——不是为实验造的短文。
- **问题集**：8 个自然语言问题，**每个问题只有一篇文档能回答**（`scripts/rag_experiment.py:92`）。
  判定目标是「文档级命中」：目标文档的任一 chunk 出现在前 5 里算命中 `hit@5`，
  并记录首次命中的位次（1.0 = 永远排第一）。
- **变量**：只改块大小（400 / 800 / 1200，重叠固定 15%）或重排器，其余不变。
- **对照**：`VectorRetriever.keyword()`（子串匹配，不需要任何 provider）作为离线地板。
- **组件**：真实 `RagPipeline` + 真实 `DashScopeEmbedder`（`qwen3.7-text-embedding-flash`）+
  `QdrantClient(path=...)` 本地嵌入模式（同样的 payload、过滤与余弦检索，只是没有服务器）。
  所有产物（SQLite、Qdrant 目录、探测用的 `.env`）都在一个临时目录里，跑完即删。

```bash
.venv/bin/python scripts/rag_experiment.py            # 需要 EMBED_* 凭据
.venv/bin/python scripts/rag_experiment.py --offline  # 只跑关键词行
```

## 6. 实验结果

### 6.1 chunk size（PLAN 5.3 / 验收标准）

同一语料、同一问题集、同一嵌入模型，只改块大小：

| chunk size | 检索方式 | chunk 数 | 平均块长 | hit@5 | 平均命中位次 | 平均延迟 | 中位延迟 |
| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 400 | 向量检索 + qwen3.7-text-embedding-flash | 421 | 342 字 | 8/8 | 1.00 | 99 ms | 97 ms |
| 400 | 关键词兜底 | 421 | 342 字 | 0/8 | 0.00 | 4 ms | 4 ms |
| 800 | 向量检索 + qwen3.7-text-embedding-flash | 216 | 667 字 | 8/8 | 1.38 | 96 ms | 97 ms |
| 800 | 关键词兜底 | 216 | 667 字 | 0/8 | 0.00 | 4 ms | 4 ms |
| 1200 | 向量检索 + qwen3.7-text-embedding-flash | 144 | 997 字 | 8/8 | 1.62 | 97 ms | 97 ms |
| 1200 | 关键词兜底 | 144 | 997 字 | 0/8 | 0.00 | 3 ms | 3 ms |

解读：

- **三档的 hit@5 一模一样（8/8）**，8 个问题都太"好找"了，判别力全部落在**位次**上：
  400 → 1.00、800 → 1.38、1200 → 1.62。块越小，越容易只有"该说的那一块"命中；
  块越大，一个块里混进多个主题，向量被平均掉，正确答案被同样沾边的块挤到后面。
- **代价在向量数**：421 / 216 / 144。从 800 降到 400 要付 95% 的额外向量（写入、存储、候选量），
  换回 0.38 个位次。ADR-0009 因此选 800/120 作为折中，并明确写「不是最优」。
- **延迟与块大小无关**（~95 ms）：一次检索只发一个 query 嵌入请求，
  端到端时间由这一次网络往返决定，不是由向量库里的点数决定。
  这个数字是**提供方噪声级别**的：同一脚本两次运行的均值差 5%～15%，
  所以表里读得出来的是"延迟不随块大小增长"，读不出来的是"800 比 1200 快 1 ms"。
- **关键词兜底 0/8**：子串匹配对自然语言问题基本无效（问题是"切分大小和重叠应该怎么定？"，
  文档里不会出现这整句话）。这正是需要向量检索的理由，也是「离线时能跑但跑不好」的诚实地板。

### 6.2 reranker（PLAN 5.7）

候选池 10 → 重排到 5，判据是重排后的前 5：

| 配置 | 候选池 | hit@5 | 平均命中位次 | 端到端延迟 | 重排耗时 |
| --- | ---: | ---: | ---: | ---: | ---: |
| IdentityReranker | 10 | 8/8 | 1.38 | 104 ms | 0.034 ms |
| ScoreReranker | 10 | 8/8 | 1.38 | 102 ms | 0.046 ms |

解读：**两者完全等价**——因为向量库本来就按 score 返回，`ScoreReranker` 拿到的顺序就是它要排的顺序。
重排自身耗时 0.03–0.05 ms（纯 Python 排序），端到端 ~100 ms 全部花在 query 嵌入上。
结论是 PLAN 5.7 那句话的字面验证：**重排要有第二个信号才有意义**，
所以 V1 不引入 cross-encoder / 托管 rerank 接口，引入条件（`hit@3` 的提升 > 延迟增量）
留给 Phase 8 用更大的标注集判定。

### 6.3 RAG OFF vs RAG ON（验收标准）

「答案词进入上下文的比例」= 该问题的答案词有多少出现在 `build_context(top_k=5)` 的文本里：

| 开关 | 上下文里的引用块 | 答案词进入上下文的比例（逐题） |
| --- | --- | --- |
| RAG ON | 5 / 5 / 5 / 5 / 5 / 5 / 5 / 5 | 100% / 100% / 100% / 100% / 100% / 100% / 100% / 100% |
| RAG OFF | （无检索，0 个引用块） | 0%（逐题为 0%，答案只能来自参数记忆） |

ON 的第 1 个问题的上下文前 3 行（`build_context` 的真实输出）：

```text
[ae26142837cf4b27#0] 架构总览：nanobot 是怎样跑起来的 (no page)
# 架构总览：nanobot 是怎样跑起来的
```

说明：OFF 一行不是"跑了一次没有检索的检索"，而是**这一次请求里没有 RAG 这一段的定义**——
所以它的答案词覆盖率必然是 0%。它想说明的是"哪些信息只能由检索提供"，
而不是"关掉开关会不会变慢"。真正把开关做进上下文的是 Phase 6（PLAN 6.5）。

### 6.4 真实服务冒烟（PLAN 验收四项）

对 `.env` 里配置的真实 Qdrant（Qdrant Cloud）+ 真实 DashScope 嵌入端点，走了一遍完整链路：

```text
$ myagent ingest docs/development.md docs/memory-design.md
INFO myagent.rag.pipeline - probed embedding dimension 1024 and recorded it in <repo>/.env
added a8361ccbbb4d80a2  11 chunk(s)  开发规范
    docs/development.md
added 44603fec319acac5  26 chunk(s)  Memory 设计：四层记忆、写入策略、检索与巩固（Phase 4）
    docs/memory-design.md
embedding dim=1024 (probed, written to .env)
2 added, 0 updated, 37 chunk(s) total

$ myagent ingest docs/development.md docs/memory-design.md     # 第二次：幂等
updated a8361ccbbb4d80a2  11 chunk(s)  开发规范
updated 44603fec319acac5  26 chunk(s)  Memory 设计：四层记忆、写入策略、检索与巩固（Phase 4）
embedding dim=1024                                             # 来自 .env，不再探测
0 added, 2 updated, 37 chunk(s) total

$ myagent docs list
44603fec319acac5    26 chunk(s)    1 page(s)  2026-09-22 04:47  docs/memory-design.md
a8361ccbbb4d80a2    11 chunk(s)    1 page(s)  2026-09-22 04:47  docs/development.md
(2 document(s), 37 chunk(s))
# Qdrant 集合里的点数：37（= 11 + 26），第二次 ingest 没有增加

$ myagent search "提交信息的格式约定是什么？" -k 2
[a8361ccbbb4d80a2#8] 开发规范 (no page)  score=0.375
    tionalcommits.org/)： ```text <type>(<scope>): <subject> ...
[a8361ccbbb4d80a2#5] 开发规范 (no page)  score=0.371
    BED_*`），项目级开关用 `MYAGENT_*` 前缀。...

$ 检索 → build_context（2378 字符）→ LLM
=== 检索到的引用 ===
  [a8361ccbbb4d80a2#8] score=0.375
  [a8361ccbbb4d80a2#5] score=0.371
  [a8361ccbbb4d80a2#9] score=0.370
=== 回答（节选）===
提交信息采用 Conventional Commits 格式： … 提交前跑 `scripts/check.sh`，pre-commit 只做快速检查，
mypy 与 pytest 由质量门负责。[a8361ccbbb4d80a2#8]
```

四点结论：

- **维度探测**：`EMBED_DIM` 留空 → 首次 ingest 探测出 1024 → 写回 `.env`
  → 第二次运行直接读配置（输出行从 `(probed, written to .env)` 变成纯 `embedding dim=1024`）；
- **幂等**：第二次 ingest 是 `0 added, 2 updated`，集合点数仍是 37；
- **引用可回跳**：模型回答末尾的 `[a8361ccbbb4d80a2#8]` 就是 `docs list` 里那篇文档的第 8 块，
  用 `myagent search` 也能重新定位到；
- **副作用是设计的一部分**：这次冒烟把 `EMBED_DIM=1024` 与两篇文档留在了
  `.env` / Qdrant 里（`.env` 与 `data/` 都被 git 忽略）。

## 7. 质量门与验收判定

```text
$ scripts/check.sh
== ruff format --check ==   83 files already formatted
== ruff check ==            All checks passed!
== mypy ==                  Success: no issues found in 53 source files
== pytest ==                591 passed（覆盖率 3550 stmts / 828 branches，100%）

$ .venv/bin/python scripts/check_doc_anchors.py
checked 998 anchor(s) in 19 document(s)
all anchors resolve
```

RAG 模块自身：`src/myagent/rag/` 10 个文件、733 stmts / 138 branches，**100%**。
整个测试套件在**没有网络**的环境里跑绿（真实的嵌入与 Qdrant 只在 §6.4 的冒烟里出现）。

| PLAN Phase 5 验收项 | 结果 |
| --- | --- |
| `myagent ingest` 后 `myagent search` 返回带 `document_id#index` 的来源 | ✅ §6.4 |
| 端到端：检索结果经 `build_context()` 注入，回答能引用到具体 chunk | ✅ §6.4（模型引用 `[a8361ccbbb4d80a2#8]`） |
| 幂等：同一文件 ingest 两次，行数与 Qdrant point 数不变 | ✅ §6.4、`tests/rag/test_pipeline.py:78` |
| 维度探测：`EMBED_DIM` 留空 → 自动探测 → collection 建立成功 | ✅ §6.4（1024） |
| 维度探测里的 `myagent config check` 报出维度 | ⚠️ **偏差**：该命令不存在（见下） |
| 离线测试不发起网络请求 | ✅ 591 项在无网沙箱里全绿；假件在 `tests/fakes.py:107`、`:185` |
| 实验表：chunk size 400/800/1200 的 hit@5 / 平均块长 / 延迟 | ✅ §6.1 |
| 实验表：「RAG OFF vs RAG ON」定性对比 | ✅ §6.3 |

**关于 `myagent config check`（唯一没有照字面完成的验收项）**：PLAN 5.4 的验收句子要求
"首次 ingest 自动探测 → collection 建立成功，且 `myagent config check` 报出维度"，
但 `config check` 是 **Phase 9 §9.2** 的产物（"启动自检——SQLite 可写、Qdrant 可达、
embedding 可调用、collection 维度一致"）。Phase 5 的 CLI 只有 `chat` / `tools` /
`memory` / `ingest` / `search` / `docs`，这次没有为了一个验收句去提前实现一个 Phase 9 的命令：
维度这件事由 ingest 的输出行（`embedding dim=1024 (probed, written to .env)`）
与 `docs list` 完成验收。这个偏差记在这里，Phase 9 实现 `config check` 时应当把它接上
（`docs/design.md` §5 的遗留表里也记了一行）。

## 8. 过程中发现并修掉的三个真实问题

### 8.1 `remember_env` 写 `os.environ` 会跨测试泄漏（真实缺陷）

**现象**：单独跑 `tests/test_settings.py::test_embedding_reads_the_env_file` 通过，
跑整个套件时失败：`assert 16 == 1536`——它读到的 `EMBED_DIM` 是上一个测试留下的 16。

**根因**：`remember_env`（`src/myagent/config/env.py:124`）为了让本次运行立刻看到新维度，
先写 `os.environ` 再改 `.env`。`monkeypatch` 无法撤销一个不是它做的写入，
于是"某个测试里 ingest 探测出的维度"会一直留到套件结束。
原先的隔离方式是 `monkeypatch.setenv("EMBED_DIM", "")`：它只能把值恢复成"进入该测试时的样子"，
而那时环境里**已经**有上一个测试留下的值。

**修复**：在 `tests/conftest.py:35` 加一个 autouse fixture `isolated_probed_dim`，
在每个测试前后从 `os.environ` 里摘掉 `EMBED_DIM`（并恢复测试开始前的值），
同时删掉三处重复的 `clean_embed_dim` fixture。
教训是"写进程环境是一种全局副作用，谁写谁负责隔离"，而不是"每个用到的地方各自打补丁"。

### 8.2 `myagent search` 的一条错误分支在生产路径上走不到

**现象**：覆盖率报告指出 `src/myagent/cli.py:223` 的 `except MissingEnvError: return 2` 从未执行。

**根因**：`VectorRetriever._embed`（`src/myagent/rag/retriever.py:123`）把 provider 的一切失败
（含缺凭据的 `MissingEnvError`）都翻译成 `EmbeddingError`，所以这条分支在"只装了假件"的
测试里够不着；而在真实链路里也一样——查询路径上唯一可能抛 `MissingEnvError` 的是
**可注入的重排阶段**（Phase 8 的 cross-encoder / 托管接口需要它自己的 key）。

**处理**：保留分支（它与 `_rag_ingest` 的语义对齐：缺配置 → 退出码 2 而不是 1），
用一条新测试把它变成真的可执行路径：一个**真实的 `RagPipeline`** + 一个会在 `rerank` 里抛
`MissingEnvError` 的重排器（`tests/test_cli.py` 的 `test_search_exits_two_when_the_query_path_needs_a_credential`）。
这不是为覆盖率造测试：`BaseReranker` 是 Phase 3 的扩展点，注入实现正是它存在的意义。

### 8.3 实验脚本的语料太小，量不出块大小的差别

**现象**：第一版实验用手写的 8 段短文（每篇约 300 字符）当语料，结果 400 / 800 / 1200 三档
切出的 chunk 数都是 8、平均块长都是 308 字——**参数根本没生效**，因为每篇文档都比最小的块还短。

**修复**：换成仓库自己的 8 篇设计文档（约 12 万字符，中英混排、有标题与代码块），
三档才切出 421 / 216 / 144 块，平均块长 342 / 667 / 997 字，实验开始有判别力。

**教训**：实验的语料尺度必须覆盖被测参数的范围。"用真实语料"不只是为了好看，
它决定了实验能不能测出东西。另外，`IdentityReranker` 与 `ScoreReranker` 打平时，
第一反应是"我写错了"；查过代码才确认这正是结论（向量库已按 score 排序），
把它写进 ADR-0009 比换一个"看起来有差别"的实验更有价值。

## 9. 结论与遗留问题

结论：

1. PLAN Phase 5 的 5.0～5.8、阶段产出与验收标准全部落地（唯一的偏差是 §7 记录的
   `config check`，它是 Phase 9 的命令）：两段流水线、三种 Loader、可配置的切分、
   维度自愈的嵌入、带 payload 过滤的 Qdrant 存、回 SQLite 解析的检索、两个重排器、
   四个 CLI 入口与带引用的 `build_context`；
2. **幂等是设计出来的，不是测出来的**：内容寻址 + `sha256 UNIQUE` + uuid5 派生点 id，
   三个机制各管一段（文档层、记录层、向量层），真实服务上第二次 ingest 是
   `0 added, 2 updated`，点数不变；
3. **默认参数有数据支撑**：chunk size 由 400/800/1200 的实验决定（ADR-0009），
   而不是"看起来合理"；同时也记录了它**不是最优**——400 的位次更好，
   只是还没贵到值得翻倍向量的程度；
4. **不引入重排模型是有证据的克制**：两个无依赖重排器在候选池 10 → top 5 上完全等价，
   重排自身 0.03–0.05 ms，说明"用同一种信号重排"不产生信息；
   引入条件（`hit@3` 提升 > 延迟增量）已经写进 ADR-0009，Phase 8 有明确的复核动作；
5. **引用是稳定的数据，不是格式**：`[document_id#index]` 的 id 能在 `docs list` 里查到，
   块号能在 SQLite 里查回原文，页码/标题给出人肉定位——三者都在 §6.4 的冒烟里验证过。

遗留问题（进入 Phase 6+ 的输入）：

| 遗留项 | 说明 / 计划 |
| --- | --- |
| `config check` 缺失 | §7；Phase 9 §9.2 实现启动自检时把维度接上 |
| RAG 还没进上下文 | `build_context()` 已就位，但没有 section 消费它；`MYAGENT_RAG_ENABLED` 与 35% 配额是 Phase 6（PLAN 6.2/6.5） |
| 标注集只有 8 问 | 三档 hit@5 全部 8/8，判别力全靠位次；Phase 8 用更大的标注集（含跨文档问题）复算 |
| 没有混合检索 | 只有向量 + 一个离线子串对照；BM25 / RRF 是 PLAN §7.1 的可选方向 |
| 没有增量更新 | 改一个字符 = 新文档（内容寻址的代价），旧文档要显式 `docs delete`；块 id 里带序号，所以改切分参数也要重建 |
| 空文档会被存下来 | 没有文字层的 PDF 会存成 0 chunk 的文档（有意如此，能看到而不是消失）；目前没有"重新尝试 OCR"的入口 |
| 块级去重 / 近重复 | 同一段文字出现在两篇文档里会被各存一份；跨文档去重没做 |
| 摄取是串行的 | 一次一个文件、一批 16 条文本；`EMBED_BATCH_SIZE` 可调，但没有并发摄取 |
| `docs list` 没有分页 | 知识库规模上千篇时 `docs list` 会很长；Phase 7 需要时再加 `--limit` |
