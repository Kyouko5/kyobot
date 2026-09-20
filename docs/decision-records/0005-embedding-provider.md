# ADR 0005：Embedding 使用阿里云 DashScope

- 状态：已接受
- 日期：2026-09-20（项目前期确定，Phase 5 实施）
- 关联：Phase 5.4（Embedding）；实现入口 `EmbeddingSettings`（`src/myagent/config/settings.py`）

## 背景

Phase 5 需要把 chunk（以及 Phase 4 的 memory 条目）转向量。Embedding 提供方决定了 key 从哪里来、
向量维度是多少、是否要引入第二家供应商，以及检索结果能否复现。

## 决策

1. 默认提供方为**阿里云 DashScope**：`EMBED_MODEL_TYPE=dashscope`，
   `EMBED_MODEL_NAME=qwen3.7-text-embedding-flash`。
2. 环境变量沿用项目确定的命名：`EMBED_MODEL_TYPE` / `EMBED_MODEL_NAME` / `EMBED_API_KEY` /
   `EMBED_BASE_URL`（可选 `EMBED_DIM`）。
3. 凭据类变量统一用短名（`LLM_*`、`EMBED_*`），项目级开关保留 `MYAGENT_*` 前缀。
4. `EMBED_API_KEY` 为空时回退到 `DASHSCOPE_API_KEY`（`openai` 类型则回退 `OPENAI_API_KEY`），
   shell 里已有的 key 不必重复声明。
5. `EMBED_BASE_URL` 留空时使用提供方默认的 OpenAI 兼容端点
   （DashScope：`https://dashscope.aliyuncs.com/compatible-mode/v1`）。
6. `EMBED_MODEL_TYPE` 支持 `dashscope` / `openai` 两种取值，配合 Phase 5 的 `BaseEmbedder` 抽象，
   换提供方只需改环境变量。
7. `EMBED_DIM` 留空表示「由服务决定」：Phase 5 用一次真实调用探测维度后写入 `.env`，
   并作为 Qdrant collection 的向量维度（ADR-0003）。

## 理由

- 与本机既有条件一致：LLM 已经走阿里云 MaaS 的 OpenAI 兼容端点，key 与网络条件都已具备，
  不需要再引入第二家供应商。
- DashScope 提供 OpenAI 兼容模式，客户端可以复用同一套 HTTP 调用路径，减少 Phase 5 的实现分叉。
- 保留 `openai` 分支既证明可插拔性，也为 Phase 8 的检索对比实验留出变量。

## 后果

- Phase 5 需实现 `DashScopeEmbedder`（OpenAI 兼容调用）与维度探测，并把维度写进 collection 创建参数。
- 维度未知是默认状态，因此不能硬编码：Phase 5 必须处理「首次运行探测 → 记录 → 复用」的流程。
- 更换 embedding 模型会改变向量空间，必须重建 collection；Phase 5 记录重建步骤。

## 备选方案

| 方案 | 不采用的原因 |
| --- | --- |
| OpenAI `text-embedding-3-*` | 需要第二家供应商与境外网络，与现有 key 体系不一致 |
| 本地 BGE / sentence-transformers | 无 key 无费用，但引入重依赖与模型下载，偏离本阶段重点；可作为 Phase 8 的对比基线 |
| 其它国产 embedding 服务 | 相对已确认的 DashScope 没有额外收益 |
