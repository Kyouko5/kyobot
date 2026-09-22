# ADR 0010：上下文预算公式与四档配额（35 / 35 / 20 / 10）

- 状态：已接受
- 日期：2026-09-22
- 关联：Phase 6.2（预算）/ 6.3（拟合顺序）/ 6.4（压缩）；
  实验数据 [`docs/records/phase-6-context.md`](../records/phase-6-context.md) §3；
  实现入口 `ContextBudget`（`src/myagent/agent/context.py:348`）、
  `DEFAULT_*_RATIO`（`src/myagent/agent/context.py:111`）、
  `AgentRuntimeConfig.context_budget`（`src/myagent/agent/runtime.py:57`）；
  设计说明 [`docs/context-design.md`](../context-design.md) §4

## 背景

Phase 5 结束时，`ContextManager.build()` 已经把七段上下文拼进一个请求，但**没有预算**：
它要么把整段对话与所有检索结果都发出去（长会话必然撞上 provider 的窗口上限而 400），
要么在某个看不见的地方随手 `[:2000]` 截断（语义被悄悄改掉）。Phase 6.2 要求把
「这次请求最多能用多少 token、每一段各分多少」变成**一个可读、可测、可调的数字**。

要定的有两个东西，都无法从原理推导，只能先立默认值再用数据复核：

1. **输入预算怎么算**——先给输出与安全余量留多少；
2. **四类来源各分多少**——对话、RAG、记忆、其他（system / summary / tools）。

约束：token 数只能**估算**。V1 的 `BaseModel.count_tokens()` 返回 `None`
（`src/myagent/models/base.py:97` 的契约已留口），所以唯一起作用的尺子是
`myagent.tokens.estimate_tokens`（CJK 1 字符≈1 token、其余 4 字符≈1 token，
`src/myagent/tokens.py:37`），上下文代码通过 `src/myagent/agent/token_budget.py`
复用它。判定指标是 PLAN 6 的验收标准：预算变小时请求**装得下**、
**按 6.1 的顺序降级**、**每次降级都在报告里可见**，而不是「省了多少 token」。

## 决策

1. **预算公式 `input_budget = context_window - max_output_tokens - 1024`**
   （`src/myagent/agent/runtime.py:84`，安全余量写成 `_SAFETY_MARGIN_TOKENS = 1024`，
   `src/myagent/agent/runtime.py:30`）。与上游 `ContextGovernor.input_budget`
   逐字同形（`nanobot/nanobot/agent/context_governance.py:693` 用的也是 1024）。
2. **算不出正预算时返回 `None`，语义是「不检查」而不是「预算为 0」**
   （`src/myagent/agent/runtime.py:84` 的 `budget if budget > 0 else None`）。
   `AgentRuntimeConfig.context_budget` 把它包成
   `ContextBudget(input_tokens=None)`（`src/myagent/agent/runtime.py:57`），
   此时任何一段都不裁剪、也不拒绝（Phase 3 的行为，留给 `myagent tools` 与单测）。
3. **四档配额 35% / 35% / 20% / 10%**：对话 35%、RAG 35%、记忆 20%、
   其他（`other_ratio`，system/summary/tools 共用）10%
   （`src/myagent/agent/context.py:111`）。两段 `required`（system / query）
   **不受配额约束**（`src/myagent/agent/context.py:107`）——配额是「可裁剪段的配额」。
4. **配额是 section 的上限，不是分配额**：`ContextBudget.quota(name)`
   返回 `max(1, int(input_tokens * ratio))`（`src/myagent/agent/context.py:380`），
   空 section 不占位；实际用量小于配额时余量**不会**被别的段借走，
   但整段的优先级降级仍按总量判断（`_fit`，`src/myagent/agent/context.py:696`）。
5. **配额跟随每次请求的预算**：`ContextBudget.with_input_tokens(limit)`
   允许请求自带 `budget_tokens` 覆盖运行时配置，比例不变、基数变
   （`src/myagent/agent/context.py:386`）。
6. **比例越界在装配时拒绝**：`__post_init__` 要求每个比例落在 `(0, 1]`、
   `input_tokens > 0`（`src/myagent/agent/context.py:368`），配置错误不会拖到请求路径上。

## 理由

- **先减输出、再减余量，是唯一能让「预算」稳定的算法**：`max_output_tokens`
  是这次生成必须留出的空间，安全余量覆盖 token 估算的误差与消息框架开销。
  先把这两块扣掉，剩下的才是可以按比例切的输入——否则比例会被「刚好塞满窗口」
  的调优反复推翻。
- **1024 与上游一致（`nanobot/nanobot/agent/context_governance.py:67`）**：
  没有新证据就不引入新数字；这也让「对齐原码」这条 Phase 6 的目标可验证。
- **「`None` = 不检查」比「0 = 拒绝一切」更安全**：`myagent tools` 之类
  没有模型配置的场景不该因为算不出预算就报错；而真正有模型配置时，
  小到算不出正预算的窗口本身就该用报错提示用户调大 `LLM_CONTEXT_WINDOW`
  （`ContextWindowExceeded` 的文案就是这么写的，`src/myagent/agent/context.py:151`）。
- **对话与 RAG 各 35% 是因为它们体量最大、也最常被牺牲**：实测（§3.1）
  预算 400 token 的请求里，未裁剪的对话 400 token、RAG 155 token，
  两者都远超其余来源，是唯二「一压就能省几百 token」的段。给它们并列最大的份额，
  等价于承认「这两段是长请求的主要成本」。
- **记忆 20% 与它的检索规模相称**：`MYAGENT_MEMORY_TOP_K` 默认取回的条数少、
  每条也短（Phase 4），20% 已经够放开手；它的价值随「是否与当前问题相关」波动，
  所以排序(先丢低重要度)比份额更影响效果。
- **其他 10% 共用**：`system`/`query` 是 `required`、不参与裁剪，
  真正会动的只有 `summary` 与 `tools`（`src/myagent/agent/context.py:670` 的截断、
  `:954` 的工具描述从尾部丢）。它们的总量本来就小，合起来一档足够。
- **比例是「起点」不是「定律」**：代码注释与本文都写明这是
  **Phase 8 实验的起点**（`src/myagent/agent/context.py:109`），
  调整只需改常量或注入 `ContextBudget`，不需要改 `build()`——
  「公式与配额各有一处实现」是 PLAN 6.2 的硬要求。

## 后果

- **token 数是估算，不是分词器**：4 字符≈1 token 对中英混排够稳，
  但对代码/JSON 可能低估。这就是安全余量 1024 存在的理由；真正的
  `count_tokens` 一旦有提供方实现，`ContextManager` 会自动用它
   （`src/myagent/agent/token_budget.py:39` 的 `TokenCounter`）。
- **配额是「软上限」**：`quota()` 只在裁剪时用；`required` 段与装配后的最终校验
  都以**总量**为准（`_measure`，`src/myagent/agent/context.py:612`）。
  也就是说「每段都没超配额」并不保证「装得下」——两者不一致时以报错为准，
  不静默截断。
- **比例固定意味着窗口越大、各段也等比例变大**：不会因为窗口很大就多给 RAG。
  这是有意的：Phase 6 要的是可预测，而非自适应；自适应留给 Phase 8 的对比实验。
- **改比例会改变实测数字**：§3.1 的降级顺序与 §3.3 的 section 集合都由这些比例决定，
  改常量后必须重跑 `scripts/context_experiment.py` 并更新实验记录。
- **`other_ratio` 同时罩住 `summary` 与 `tools`**：两者互斥地竞争那 10%，
  谁用量大谁先被压；目前 `summary` 另有 600 token 的独立上限
  （`DEFAULT_SUMMARY_TOKENS`，`src/myagent/agent/compaction.py:61`）。

## 备选方案

| 方案 | 不采用的原因 |
| --- | --- |
| 不设预算，超了让 provider 报 400 | 错误难以定位（看不出是哪一段超的），且长会话每次都要撞墙才发现 |
| 按消息条数而不是 token 裁剪 | 一条消息可以相差两个数量级（一页文档 vs. 一句「好」），条数无法代表成本 |
| 引入真实分词器（tiktoken 等） | 提供方可能是非 OpenAI 的兼容端点；契约里已留 `count_tokens`，等真实提供方再实现 |
| 安全余量取 0（「刚好塞满」） | 估算误差会把请求推过窗口；1024 与上游一致，也不构成可观浪费 |
| 固定 token 配额（如对话 4096、RAG 4096） | 窗口大小与模型千差万别，固定值在小窗口上直接失效、在大窗口上浪费 |
| 让「剩余配额」跨段借用 | 会让「RAG 挤掉对话」这类不可预测的耦合出现，报告也更难读 |
| 配额自适应（按历史用量动态调） | 先要可预测的行为；自适应需要离线数据，属 Phase 8 |
| 把 `system`/`query` 也纳入配额 | 它们是身份与当前问题，裁掉就等于答非所问；保持 `required` 并在装不下时报错 |
