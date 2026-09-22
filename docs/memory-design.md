# Memory 设计：四层记忆、写入策略、检索与巩固（Phase 4）

- 状态：已实现（Phase 4）
- 关联文档：[`docs/memory.md`](./memory.md)（上游 nanobot 的记忆机制）、
  [`docs/decision-records/0008-layered-memory.md`](./decision-records/0008-layered-memory.md)（决策）、
  [`docs/records/phase-4-memory.md`](./records/phase-4-memory.md)（实验与质量门）
- 代码入口：`src/myagent/memory/`（13 个文件 2284 行）；装配点 `src/myagent/runtime.py:87`（`build_memory`）

## 0. 一句话

上游只有**一层**长期记忆：一个人类可读的文本文件 `MEMORY.md`（`agent/memory.py:229` 读写、
`agent/memory.py:253` 拼进上下文），「记住什么」完全交给模型自己往文件里写。它回答不了三个问题——
**这条记忆属于哪一层、该活多久、召回时怎么排序**。

Phase 4 把它拆成四层（Working / Episodic / Semantic + Retriever），落进 ADR-0003 选定的
SQLite + Qdrant：**记录（文本、类型、重要度、来源）在 SQLite，向量在 Qdrant，用 `memory_id` 关联**。
写入不是「把这一轮对话追加进去」，而是一条带策略的流水线；召回不是「把文件塞进 prompt」，
而是「向量检索 + 时间衰减 + 关键词兜底」，并且**失败时降级而不是报错**。

## 1. 分层模型（PLAN 4.0）

```text
MemoryManager（门面：write / recall / context / consolidate，src/myagent/memory/manager.py:58）
├── WorkingMemory     本轮对话窗口（不落库，直接从 Session 转录构造，src/myagent/memory/working.py:23）
├── EpisodicMemory    「发生过什么」：事件、任务结论、读过的论文（src/myagent/memory/episodic.py:39）
├── SemanticMemory    「我知道什么」：稳定偏好、长期事实、项目知识（src/myagent/memory/semantic.py:38）
└── MemoryRetriever   embedding + 向量检索 + 时间衰减 + 关键词兜底（src/myagent/memory/retriever.py:53）
```

| 层 | 回答什么 | 生命周期 | 写入触发 | 进向量库 | 退出路径 |
| --- | --- | --- | --- | --- | --- |
| Working | 这次对话到哪了 | 单次会话 | 每轮自动 | 否 | 会话结束即丢弃 |
| Episodic | 过去发生过什么 | 天～周 | 归档检查点 + 抽取器 | 是（带时间戳，参与衰减） | 巩固为 Semantic，或人工删除 |
| Semantic | agent 知道什么 | 长期 | 抽取器 / 显式写入 / 巩固 | 是（不衰减） | 人工删除（`myagent memory forget`） |

三条设计约束（PLAN 4.0 原文，实现位置在括号里）：

1. **不重复存储对话原文**：Working Memory 直接从 `SessionStore` 的历史构造
   （`src/myagent/memory/working.py:34` 的 `recent_turns` 调
   `SessionStore.get_or_create(key).transcript()`，对齐上游 `session/manager.py:344`
   的 `get_history`），所以"一轮对话"只有一处落盘。
2. **Session 与 Memory 的边界不变**：Session 是可重放的原文，Memory 是被提炼的结论。
   两者共用同一个 SQLite 文件但**不同表**（`memories` / `memory_vectors`，
   `src/myagent/memory/sqlite_store.py:49`），Session 仍然是 JSONL。
3. **写入有策略**：不是所有对话都进长期记忆（见 §4），否则检索会被噪声淹没。

### 1.1 一条记忆的一生

```text
一轮对话（user + assistant + 工具结果）
   │  loop 的 save 阶段之后：AgentLoop._observe_turn（src/myagent/agent/loop.py:239）
   ▼
MemoryManager.remember → MemoryExtractor.extract（src/myagent/memory/extractor.py:150）
   │  规则兜底（我是/我偏好/我的项目是）+ LLM 抽取（JSON schema）
   ▼
apply_policy：拆句 → 写/不写清单 → 重要度 → 单轮条数上限（src/myagent/memory/extractor.py:184）
   │
   ▼
dedup：与最近 8 条精确匹配 + 余弦 > 0.95（src/myagent/memory/extractor.py:200）
   │
   ▼
MemoryManager.write（src/myagent/memory/manager.py:159）
   │  ① SQLite 先落盘（真相来源）  ② 需要时 embed  ③ Qdrant upsert  ④ 记 memory_vectors
   ▼
   …… 之后每次对话：recall（src/myagent/memory/manager.py:136）→ MemoryRetriever.search（src/myagent/memory/retriever.py:79）
   ▼
Consolidator：Episodic → Semantic（src/myagent/memory/consolidator.py:92，`myagent memory consolidate` 触发）
```

## 2. 数据结构（PLAN 4.1）

`MemoryRecord`（`src/myagent/memory/types.py:66`）是唯一的数据结构，四层共用：

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `id` | `str` | `uuid4().hex`（32 位无连字符，`src/myagent/memory/types.py:82` 的 `create` 生成）；Qdrant 的 point id 与 SQLite 主键都用它 |
| `kind` | `"episodic" \| "semantic"` | 层（`src/myagent/memory/types.py:36`、`:37`）；决定是否参与时间衰减、检索时如何过滤 |
| `text` | `str` | **一条只承载一个事实**，便于独立检索、独立失效、独立删除 |
| `session_key` | `str \| None` | 来源会话（Working 层不用）；也是 `myagent memory list` 里"谁说的" |
| `created_at` | `datetime`（带时区） | 写入时间；衰减与"最新优先"都用它 |
| `importance` | `float` 0..1 | 写入阈值（≥0.5）与同轮排序用；规则命中固定 `0.7`（`src/myagent/memory/types.py:46`） |
| `source` | `str` | `manual` / `rule` / `llm` / `consolidation`（`src/myagent/memory/types.py:42`），实验里用来区分抽取方式 |
| `metadata` | `dict` | 额外信息，如巩固来源 `consolidated_from` |
| `consolidated_at` | `datetime \| None` | 巩固游标（PLAN 4.8）：`NULL` = 还没被折叠进 Semantic |

三个配套类型：

- `MemoryHit`（`src/myagent/memory/types.py:144`）= `record` + `score` + `reason`；
  `reason` 是 `"vector"` 或 `"keyword"`，CLI 直接打印成 `via=vector`（`src/myagent/cli.py:142`）。
- `MemoryContext`（`src/myagent/memory/types.py:157`）= 一次召回的完整结果：`hits` / `degraded` / `note`。
  `degraded=True` 表示"向量库不可用，答案只来自关键词"，`note` 是给人看的原因。
- `parse_datetime` / `utcnow`（`src/myagent/memory/types.py:49`、`:59`）：SQLite 边界上只认 ISO8601 字符串。

## 3. 存储层（PLAN 4.5）

### 3.1 SQLite：记录与向量状态（`src/myagent/memory/sqlite_store.py`）

```sql
memories(                          -- 文本与元数据：真相来源
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    text TEXT NOT NULL,
    session_key TEXT,
    created_at TEXT NOT NULL,      -- ISO8601
    importance REAL NOT NULL,
    source TEXT NOT NULL,
    metadata TEXT NOT NULL,        -- JSON
    consolidated_at TEXT           -- 巩固游标：NULL = 待巩固
)
memory_vectors(                    -- 向量落库状态：避免重复 embedding
    memory_id TEXT PRIMARY KEY REFERENCES memories(id) ON DELETE CASCADE,
    collection TEXT NOT NULL,
    model TEXT NOT NULL,
    dim INTEGER NOT NULL,
    embedded_at TEXT NOT NULL
)
-- 索引：memories(kind, created_at)、memories(session_key)（src/myagent/memory/sqlite_store.py:49）
```

三个实现决策：

| 决策 | 为什么 | 位置 |
| --- | --- | --- |
| 一次调用一个连接（不持有长连接） | CLI、Loop 的线程池、测试都会用同一个 store，跨线程共享 `sqlite3` 句柄容易踩坑；每次调用重跑 `CREATE TABLE IF NOT EXISTS` 是幂等的 | `src/myagent/memory/sqlite_store.py:263` |
| 用 `ON CONFLICT DO UPDATE` 而不是 `INSERT OR REPLACE` | `REPLACE` 会先删行，级联把 `memory_vectors` 一并删掉，等于"重新存一次就把向量状态丢了、被迫重新 embedding" | `src/myagent/memory/sqlite_store.py:81` |
| 关键词检索自己分词（拉丁词 + 单个 CJK 字） | 不引 jieba 这类重依赖；它只需要回答"这条记忆有没有被提到"，并诚实承认自己不是排序模型 | `src/myagent/memory/sqlite_store.py:100` |

关键词打分 = 命中的查询词占比（`src/myagent/memory/sqlite_store.py:166` 的 `search`），
只做兜底（PLAN 4.4/4.7），**不做**混合排序。

### 3.2 Qdrant：向量与过滤（`src/myagent/memory/vector_index.py`）

- **独立 collection**：`MYAGENT_QDRANT_MEMORY_COLLECTION`，默认 `myagent_memories`
  （`src/myagent/config/settings.py:146`），与文档向量的 `myagent_documents` 分开。
  原因见 ADR-0008：记忆按 id 删除、文档整篇重灌，混在一起会让一次清理误伤另一类数据。
  `QdrantSettings.__post_init__` 在两者相等时直接抛 `ValueError`（`src/myagent/config/settings.py:180`）。
- **payload 只有四个字段**（`src/myagent/memory/vector_index.py:198`）：

  ```json
  {"memory_id": "…", "kind": "semantic", "session_key": "cli:default", "created_at": "2026-09-20T09:48:44+00:00"}
  ```

  `kind` 是过滤字段（`src/myagent/memory/vector_index.py:235` 的 `_kind_filter` → `FieldCondition`），
  `created_at` 供重排使用；文本留在 SQLite，payload 越小索引越便宜。
- **错误只有一种形态**：所有 SDK 调用都过 `_call`（`src/myagent/memory/vector_index.py:179`），
  失败一律变成 `MemoryIndexError`，消息里带 URL 与 collection 名。
  调用方只需要处理一种情况——"Qdrant 挂了，降级"——而不是各种 `httpx` 异常。
- **id 归一化**（真实环境踩到的坑，见 §7.1）：Qdrant 服务器会把 point id 规范化成带连字符的
  UUID。`_memory_id`（`src/myagent/memory/vector_index.py:214`）因此**优先读 payload 里的 `memory_id`**，
  只有在 payload 里没有时才退回 `str(point.id)`——否则用 `uuid4().hex` 存进去的记录
  会因为 `store.get(带连字符的 id)` 查不到而静默退回关键词检索。

`MemoryIndex`（`src/myagent/memory/vector_index.py:61`）是本模块唯一的契约（`ensure_collection` / `upsert` /
`search` / `delete` / `count`），只有 `vector_index.py` import `qdrant_client`；
离线测试用 `tests/fakes.py:131` 的 `DictionaryIndex` 替换它。

## 4. 写入策略：什么该记，什么不记（PLAN 4.6）

**结论：LLM 负责提议，代码负责裁决。** 上游 Dream 让模型直接改文件（`agent/memory.py:543`），
我们保留"让模型自己写记忆"的思路，但每一层输出都要过校验，非法输出丢弃并记一条 warning
（`src/myagent/memory/extractor.py:246`）。

```text
一轮对话
   ├─▶ 规则兜底：命中「我是 / 我偏好 / 我的项目是 / I prefer / my project is」的句子 → Semantic(importance=0.7)
   └─▶ LLM 抽取：JSON {"memories": [{"text", "kind", "importance"}]}（prompt 见 src/myagent/memory/extractor.py:62）
        │
        ▼  apply_policy（src/myagent/memory/extractor.py:184）
   ① 拆句：超过 500 字符先按句号拆，保证"某一条事实"能被单独删除（src/myagent/memory/extractor.py:271）
   ② 写/不写清单（下表）
   ③ 重要度 ≥ 0.5
   ④ 单轮最多 3 条，按 importance 取前 N（src/myagent/memory/extractor.py:184）
        │
        ▼  dedup（src/myagent/memory/extractor.py:200）
   ⑤ 与最近 8 条做「归一化文本精确匹配 + 余弦 > 0.95」去重；同一批候选之间也互相去重
        │
        ▼  MemoryManager.write（src/myagent/memory/manager.py:159）
   ⑥ 先写 SQLite → 需要时 embedding → Qdrant upsert → 写 memory_vectors
```

### 4.1 「写 / 不写」清单

| 内容 | 写？ | 判定位置 | 例子 |
| --- | --- | --- | --- |
| 稳定偏好 | ✅ 写（semantic） | `_FACT_PATTERNS`（`src/myagent/memory/extractor.py:99`） | 「我偏好用 Python 写数据处理脚本。」 |
| 长期事实 / 项目知识 | ✅ 写（semantic） | 同上 | 「我的项目是 kyobot，一个个人 Agent Framework。」 |
| 「发生过什么」 | ✅ 写（episodic） | LLM 抽取路径 | 「读了 RAG 综述论文 A 篇，笔记在 workspace/notes/rag-a.md。」 |
| 闲聊 | ❌ 不写 | `_CHITCHAT`（`src/myagent/memory/extractor.py:79`），**只匹配整句** | 「你好呀！」「谢谢，辛苦了。」 |
| 一次性查询 | ❌ 不写 | `_ONE_OFF`（`src/myagent/memory/extractor.py:88`） | 「现在几点了？」「今天天气怎么样？」 |
| 工具原始输出 | ❌ 不写 | `_looks_like_tool_output`（`src/myagent/memory/extractor.py:322`，判断 JSON/数组形状） | `{"temperature": 21.5, "city": "Shanghai"}` |
| 密钥 / 隐私 | ❌ 不写 | `_SECRETS`（`src/myagent/memory/extractor.py:89`：`sk-…`、`password:`、`Bearer …`、手机号、身份证号） | 「我的 API key 是 sk-abcdef1234567890」 |

两条容易误读的细节：

- **闲聊只匹配整句**：「你好，我在研究 RAG」是事实，不会被 `_CHITCHAT` 吃掉，
  因为它要求整句只剩问候语与语气词（正则以 `$` 收尾）。
- **上限写在代码里，不写在 prompt 里**：prompt（`src/myagent/memory/extractor.py:62`）里也写了
  「Max 3 memories / concise」，并明确 `importance` 是「长期价值」而不是「置信度或相关度」——
  但那只是提示；真正保证 500 字符/条、3 条/轮、`importance ≥ 0.5` 的是
  `apply_policy` 与 `_split`（这也是 PLAN 4.1 要求的「在写入层强制」）。

`parse_candidates`（`src/myagent/memory/extractor.py:358`）把模型输出解析成候选记录：容忍 markdown 代码块、
字段缺失与类型错误，逐条校验，**坏的那条丢掉而不是整批丢掉**。

## 5. 检索（PLAN 4.7）

```text
query
 ├─ 短 query（≤8 字符）且 kind=semantic？ → 先走关键词（embedding 对「RAG？」这种短句不敏感）
 ├─ embed（复用 Phase 3 的 BaseEmbedder；传输层在 Phase 5 搬到 src/myagent/rag/embedder.py:74）
 ├─ Qdrant search(filter=kind, top_k=5)
 ├─ 时间衰减重排：score = cosine × 0.5 ** (age_days / half_life_days)（只作用于 episodic）
 └─ 向量没找到 → 关键词兜底
```

四个可观察的行为（都在 `src/myagent/memory/retriever.py`）：

| 行为 | 说明 | 位置 |
| --- | --- | --- |
| 衰减只作用于 Episodic | Semantic 是"我知道什么"，不该被时间吃掉；`decay()` 对非 episodic 返回 `1.0` | `src/myagent/memory/retriever.py:125` |
| 命中都带 `memory_id` | 返回的 `record` 是从 SQLite 重新读出来的（`src/myagent/memory/retriever.py:145`），所以"点还在、记录已删"的陈旧向量不会漏进答案；Phase 8 用 `MemoryContext.ids` 算 hit@k | `src/myagent/memory/retriever.py:145` |
| 失败降级，不抛异常 | Qdrant 或 embedding 任意一个失败 → `degraded=True` + 可读 `note` + 关键词结果；`src/myagent/agent/loop.py:224` 的 `_recall_memories` 还会把异常兜成"这轮没有记忆" | `src/myagent/memory/retriever.py:79` |
| 同分按新→旧 | 排序键是 `(-score, -created_at)`，衰减之后的并列取更新的那条 | `src/myagent/memory/retriever.py:145` |

## 6. 巩固：Episodic → Semantic（PLAN 4.8，对应上游 Dream）

- **触发**：显式命令 `myagent memory consolidate`（含 `--dry-run`），**不做定时任务**
  （PLAN 4.8 明确避免运维复杂度；上游是 `/dream` 命令与 gateway 心跳触发，
  `nanobot/nanobot/command/builtin.py:474`）。
- **输入**：SQLite 里 `kind="episodic"` 且 `consolidated_at IS NULL` 的记录，
  按 `created_at` 增量（`src/myagent/memory/sqlite_store.py:199` 的 `pending_episodic`）。
- **过程**（`src/myagent/memory/consolidator.py:92`）：
  1. 按词项 Jaccard ≥ 0.2 聚类（`src/myagent/memory/consolidator.py:201`），把讲同一件事的记录归到一起；
  2. 有模型时请模型合并；**模型返回的条数不少于输入条数就判定"它没有在合并"**，
     退回规则合并（`src/myagent/memory/consolidator.py:119`）——规则合并是把同簇原文用「；」连起来，不会丢信息；
  3. 写 Semantic 记录（`source="consolidation"`，metadata 记 `consolidated_from`）；
  4. **只有写成功才 `mark_consolidated`**（`src/myagent/memory/consolidator.py:92`），
     写失败/`dry_run` 时所有记录保持 pending——语义对齐上游
     `agent/memory.py:619` 的 `dream_run_completed`：只有成功才前移游标。
- **输出**：新的 Semantic 记录；被折叠的 Episodic 打上 `consolidated_at`，
  默认仍在库里（可检索），但没有被删除——删除是 `myagent memory forget` 的事。

### 6.1 与上游 Dream 的对照

| 维度 | 上游 nanobot | 本项目 Phase 4 |
| --- | --- | --- |
| 触发 | `/dream` 命令（`nanobot/nanobot/command/builtin.py:474`）、gateway 心跳 | `myagent memory consolidate`（显式，无定时） |
| 输入游标 | `.dream_cursor` 文件 + `read_unprocessed_history`（`agent/memory.py:399`） | `memories.consolidated_at IS NULL`（`src/myagent/memory/sqlite_store.py:199`） |
| 游标前移时机 | `dream_run_completed` 判定成功后才前移（`agent/memory.py:619`） | 写成功后才 `mark_consolidated`（`src/myagent/memory/consolidator.py:92`） |
| 输出形态 | 模型用工具直接改 `MEMORY.md` / `SOUL.md`（`build_dream_tools`，`agent/memory.py:575`） | 模型只输出 JSON 候选，代码落 SQLite + Qdrant（`src/myagent/memory/consolidator.py:140`） |
| 合并策略 | 全交给模型（prompt 见 `agent/memory.py:543`） | 模型合并 + 规则兜底；模型"没在合并"时退回规则（`src/myagent/memory/consolidator.py:119`） |
| 存储 | 单文件 `memory/MEMORY.md`（`agent/memory.py:229`） | SQLite 记录 + Qdrant 向量，两层 kind |
| 可解释性 | 读文件即可 | `source` / `consolidated_from` / `MemoryHit.reason`，实验可复算（`docs/records/phase-4-memory.md`） |
| 会话归档 | `archive_session` 把会话摘要写进记忆（`agent/memory.py:996`） | 会话原文留在 SessionStore；记忆只存提炼结果（PLAN 4.0 约束 2） |

## 7. 装配：谁在什么时候调用记忆

```text
myagent chat ──▶ src/myagent/runtime.py:41 build_agent
                     └─ memory=build_memory(...)（src/myagent/runtime.py:87）
myagent memory … ─▶ src/myagent/runtime.py:87 build_memory（只要记忆，不要 Loop）

AgentLoop（src/myagent/agent/loop.py:131 收 memory）
 ├─ build 阶段：_recall_memories（src/myagent/agent/loop.py:224）→ MemoryManager.recall → ContextRequest.memories
 └─ save 阶段：_observe_turn（src/myagent/agent/loop.py:239）→ MemoryManager.observe → extract → write
```

关键边界：`src/myagent/agent/` **不 import `myagent.memory`**。Loop 只认
`MemoryProvider` 这个 Protocol（`src/myagent/agent/context.py:90`）：
`recall(query, session_key) -> Sequence[ContextItem]` 与 `observe(session_key, messages)`。
两个方向都失败即降级（`src/myagent/agent/loop.py:224`、`:239` 里 `except Exception` → warning），
因为"记忆"是增强项：Qdrant 挂掉应该损失上下文质量，而不是让这一轮对话失败。

### 7.1 配置项（`src/myagent/config/settings.py:280`）

| 环境变量 | 默认 | 作用 |
| --- | --- | --- |
| `MYAGENT_MEMORY_ENABLED` | `true` | 总开关；`false` = 不召回、不自动写，`memory add` 也会拒绝（`memory list` 仍可查看已有记录） |
| `MYAGENT_MEMORY_HALF_LIFE_DAYS` | `30` | Episodic 衰减半衰期 |
| `MYAGENT_MEMORY_TOP_K` | `5` | 一次召回几条 |
| `MYAGENT_MEMORY_MAX_TEXT_CHARS` | `500` | 单条记忆字符上限（写入层强制） |
| `MYAGENT_MEMORY_MAX_RECORDS_PER_TURN` | `3` | 单轮最多写几条 |
| `MYAGENT_QDRANT_MEMORY_COLLECTION` | `myagent_memories` | 记忆向量集合（必须与 `MYAGENT_QDRANT_COLLECTION` 不同） |

阈值类常量（`min_importance=0.5`、`dedup_threshold=0.95`、`dedup_recent=8`、
`short_query_chars=8`）目前是代码默认值（`src/myagent/config/settings.py:116`），可以通过
`MemorySettings(...)` 覆盖，但没有对应的环境变量——这是有意的最小化：它们是实验参数，
不是部署参数。

## 8. CLI（PLAN 4.9）

```bash
myagent memory list --kind semantic -n 20     # 看记住了什么（src/myagent/cli.py:120）
myagent memory search "我的研究方向" -k 5      # 召回 + 分数 + 来源路径（src/myagent/cli.py:133）
myagent memory add "用户偏好 Python" --kind semantic --importance 0.8   # 手工写一条（src/myagent/cli.py:150）
myagent memory consolidate --dry-run          # 只看会合并什么（src/myagent/cli.py:176）
myagent memory forget <memory_id>             # 删除一条（src/myagent/cli.py:189）
```

`memory search` 会打印 `score=0.686 via=vector` 这样的行，并在降级时把
`note` 打到 stderr（`src/myagent/cli.py:142`）——"没有结果"和"向量库不可用"必须能区分开。

## 9. 已知限制与不做什么

| 限制 | 说明 / 计划 |
| --- | --- |
| 去重阈值 0.95 拦不住改写 | 实验里同一事实换个说法（余弦 0.82）仍会写进库；精确匹配只能挡住完全相同的句子。阈值下调会误杀"相似但不同"的事实，留给 Phase 8 用数据决定（见 `docs/records/phase-4-memory.md` §8.1） |
| 关键词分词不是排序模型 | 命中词占比对长句不公平；真正的混合检索（BM25 / RRF）在 Phase 5+ 的 `§7.1 Hybrid Retrieval` |
| 巩固不删除原始 Episodic | 折叠后原记录仍在库里（标了 `consolidated_at`），检索时可能与 Semantic 同时命中；清理策略留给后续 |
| 没有定时巩固 / 后台任务 | 显式命令触发，避免引入调度与并发写 |
| `:memory:` SQLite 不支持 | 一次调用一个连接，内存库每次都是新的空库；测试一律用 `tmp_path`（`src/myagent/memory/sqlite_store.py:263` 的注释） |
| 单进程假设 | SQLite 靠文件锁，Qdrant 靠服务端；没有跨进程的写入去重 |
