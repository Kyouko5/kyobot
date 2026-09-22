# ADR 0008：分层记忆的写入策略、向量命名空间与巩固游标

- 状态：已接受
- 日期：2026-09-20
- 关联：Phase 4（4.0 分层 / 4.5 存储 / 4.6 写入策略 / 4.8 巩固）；
  实现入口 `src/myagent/memory/`、装配点 `src/myagent/runtime.py:121`；
  设计说明 [`docs/memory-design.md`](../memory-design.md)

## 背景

上游 nanobot 只有一层长期记忆：一个 `MEMORY.md` 文件（`agent/memory.py:229` 读写、
`:253` 注入上下文），Dream 定时把日记交给模型，由模型用工具直接改写文件
（`agent/memory.py:543` 的 prompt、`:575` 的 `build_dream_tools`）。这套机制有三个
无法回避的问题，正是 PLAN Phase 4 要回答的：

1. **没有分层**：「用户偏好 Python」和「今天读了论文 A」写在一起，无法表达不同的生命周期；
2. **没有写入策略**：模型写什么就存什么，闲聊、一次性查询、工具原始输出都可能进长期记忆；
3. **没有召回排序**：整份文件进 prompt，既没有相似度、也没有时间权重，文件越长越糊。

同时有三个新的工程约束：ADR-0003 已经定了 SQLite + Qdrant；ADR-0005 定了 embedding 来源；
Phase 3 已经用 `Protocol` + 装配注入立好了 `BaseMemory` 契约。本 ADR 记录 Phase 4 在这三条
之上做的选择。

## 决策

1. **四层模型**：`MemoryManager`（门面）+ Working / Episodic / Semantic + `MemoryRetriever`，
   字段与生命周期见 `docs/memory-design.md` §1。Working 不落库（从 `SessionStore` 构造，
   `src/myagent/memory/working.py:34`），Episodic 参与时间衰减，Semantic 不衰减。
2. **记录的真相在 SQLite，向量只是索引**：`memories` 存文本/类型/重要度/来源，
   `memory_vectors` 记「这条记录在哪个 collection、用哪个模型、什么维度」；
   检索命中后**回到 SQLite 取记录**（`src/myagent/memory/retriever.py:144`），
   所以陈旧向量不会漏进答案，向量库挂了记录也还在、还能按关键词搜。
3. **记忆用独立 collection**：`MYAGENT_QDRANT_MEMORY_COLLECTION`，默认 `myagent_memories`，
   与文档向量的 `myagent_documents` 分开；`QdrantSettings` 在两者相等时直接抛
   `ValueError`（`src/myagent/config/settings.py:182`）。
   理由：记忆按 id 逐条删除、文档整篇重灌，生命周期与清理粒度不同，混在一起会让
   「删掉一篇论文」的 `delete(document_id)` 有误伤记忆的可能。
4. **LLM 提议，代码裁决**：抽取器（`src/myagent/memory/extractor.py:150`）同时跑规则兜底与
   LLM 抽取，之后统一过写入策略（`:184`，拆句 + 写/不写清单 + 重要度 + 单轮条数上限）
   与去重（`:200`）。**上限写在代码里，不靠 prompt 自觉**；非法输出逐条丢弃并记 warning。
5. **明确的「不写」清单**：闲聊（整句匹配）、一次性查询、工具原始输出、密钥与隐私，
   见 `docs/memory-design.md` §4.1 的表格（判定位置 `src/myagent/memory/extractor.py:79`、`:88`、`:89`、`:322`）。
6. **衰减只作用于 Episodic**：`score = cosine × 0.5 ** (age_days / half_life_days)`，
   半衰期默认 30 天（`src/myagent/memory/retriever.py:125`）；Semantic 的 factor 恒为 1.0——
   重要偏好不该被时间吃掉（PLAN 4.4）。
7. **巩固只在成功时前移游标**：`Consolidator.consolidate`（`src/myagent/memory/consolidator.py:92`）
   先写 Semantic、写成功后才 `mark_consolidated`；`dry_run` 与失败都保持 pending。
   语义对齐上游 `agent/memory.py:619` 的 `dream_run_completed`。
8. **模型"没在合并"就不采纳**：合并结果条数不少于输入条数时退回规则合并
   （`src/myagent/memory/consolidator.py:119`），规则合并用「；」拼接同簇原文，不会丢信息。
9. **失败降级，不抛异常**：检索把 Qdrant / embedding 的失败转成
   `MemoryContext(degraded=True, note=...)`（`src/myagent/memory/retriever.py:79`）；Loop 侧再把异常兜成
   「这轮没有记忆」（`src/myagent/agent/loop.py:247`、`:239`）。记忆是增强项，不是依赖。
10. **`agent` 不 import `myagent.memory`**：Loop 只认 `MemoryProvider`
    （`src/myagent/agent/context.py:180`，`recall` + `observe` 两个方法），
    `MemoryManager` 实现它；装配仍在 `myagent.runtime`（`build_memory`，`src/myagent/runtime.py:121`）。

## 理由

- **分层对应三种正确性判据**：「偏好记错了」会长期误导后续对话，「上周读了什么记错了」
  只影响一次回忆，「这轮聊到哪」只需本次会话正确。用一种存储、一种生命周期去承载三者，
  必然在某一层将就。
- **记录与向量分离让写路径可恢复**：先写 SQLite 再 embed/upsert，任何一步失败都只损失
  「召回质量」而不是「记忆本身」；`memory_vectors` 让"哪些还需要 embedding"成为可查询状态，
  而不是内存里的待办（这也是离线测试能覆盖整个写入路径的原因，PLAN 4.9）。
- **独立 collection 是生命周期问题，不是洁癖**：混用一个 collection 时，`delete` 的粒度
  只能是「id 列表」，而两类数据的删除时机完全不同；分开后两个清理动作互不影响，
  代价只是配置项 +1。
- **代码裁决而不是信任模型**：模型输出是概率性的，而"这条要不要记"是可验证的规则。
  prompt 里也写了上限与禁区，但那只是提高命中率；真正的保证在 `apply_policy` 与 `_is_writable`。
- **规则兜底是离线地板**：规则命中「我是 / 我偏好 / 我的项目是」等模式时直接产出 Semantic
  记录（`importance=0.7`），不需要任何 provider。它让「没有 key 也能跑通并测试整套记忆」
  成立（`tests/test_memory.py`），也是实验里规则一列的来源。
- **衰减只给 Episodic**：时间衰减回答的是「最近发生了什么」，不是「什么更重要」；
  把 Semantic 也衰减，等于让 agent 慢慢忘掉用户偏好——正是 PLAN 4.4 要避免的。
- **游标语义与上游一致**：「只有成功才前移」是把"巩固"做成幂等操作的关键；否则一次失败的
  运行会让记录既没被折叠、又被标记为已处理。
- **Protocol 而不继承**：Phase 3 已经证明契约可以用结构类型（ADR-0007），Phase 4 的
  `SQLiteMemoryStore` / `QdrantMemoryIndex` 因此不需要继承任何基类；
  `MemoryProvider` 让 `agent` 侧保持零依赖，Phase 6 换记忆实现不需要动 Loop。

## 后果

- **一次记忆写入最多 4 次 I/O**：SQLite 写入、embedding 调用、Qdrant upsert、记
  `memory_vectors`。前 1 步是强一致的，后 3 步是尽力而为（失败只记 warning）。
- **去重阈值是概率性的**：余弦 > 0.95 能挡住"同一句话换个说法里最像的那些"，但实验里
  0.82 的改写仍然会写进库（`docs/records/phase-4-memory.md` §8.1）。
  精确匹配（归一化文本）能保证"同一句原话"只存一条。阈值下调到 0.85 会误杀"相似但不同"的事实，
  需要 Phase 8 的数据来决定，现在保持 0.95 并如实记录。
- **巩固不删除原始 Episodic**：折叠后原记录仍在库里（带 `consolidated_at`），
  可能与新的 Semantic 同时被召回。这是有意的保守选择：删除不可逆，留给人工 `forget`。
- **没有定时巩固 / 后台任务**：只有 `myagent memory consolidate`。
  上游的 `/dream` 是命令 + gateway 心跳（`nanobot/nanobot/command/builtin.py:474`），
  我们不做调度器，避免并发写与运维复杂度（PLAN 4.8 的选择）。
- **关键词兜底不是排序模型**：分词是「拉丁词 + 单个汉字」，打分是命中词占比，
  只用于短 query 与降级路径；混合检索（BM25 / RRF）留给 Phase 5+。
- **`:memory:` SQLite 不可用**：store 一次调用一个连接（`src/myagent/memory/sqlite_store.py:263`），
  内存库每次都是新的空库；测试统一用 `tmp_path`。

## 备选方案

| 方案 | 不采用的原因 |
| --- | --- |
| 继续用单文件 `MEMORY.md`（上游做法） | 无法分层、无法按相似度召回、无法逐条删除或失效；文件越长，塞进 prompt 的噪声越多 |
| 让模型用工具直接改写记忆文件（上游 dream tools） | 可审计性差、写入不可校验；改成「模型出 JSON 候选 + 代码裁决」，模型的能力仍然用上，但错误被挡住 |
| 记忆与文档共用一个 collection | 清理粒度不同会互相误伤（见决策 3）；payload 过滤能区分，但删除语义区分不了 |
| 记忆全部放 SQLite（`sqlite-vec`） | 少一个组件，但放弃 Qdrant 的 payload 过滤与运维形态，也与 ADR-0003 冲突 |
| 给 `MemoryRecord` 加 `tags` 做分类而非 `kind` 两层 | `kind` 决定的是生命周期与衰减规则（可计算的语义），标签只是检索条件；两者不冲突，Phase 7 需要时再加 |
| 定时巩固（后台任务 / 心跳） | 引入调度、并发写与状态恢复；显式命令足够，且让「游标只在成功时前移」更容易验证 |
| 让 Loop 直接 import `MemoryManager` | 违反 Phase 3 的依赖方向（`agent` 不依赖 `memory`），Phase 6 改记忆实现就得动 Loop |
