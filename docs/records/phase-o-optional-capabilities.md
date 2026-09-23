# Phase O 工作记录：基础模式与可选 Memory / RAG

- 日期：2026-09-23
- 状态：已完成

## 结论

只配 LLM 即可运行对话；Memory 与 RAG 默认关闭（`src/myagent/config/settings.py:113`、`src/myagent/config/settings.py:137`）。网页端可分别启用两项能力，配置 Embedding 与 Qdrant 后保存到同一份 `.env`，运行中的 Agent 随即重建（`src/myagent/gateway/config.py:108`、`src/myagent/gateway/app.py:131`）。

## 问题与方案

原有默认开关为开启状态，初次对话会进入可选向量能力路径。现改为默认关闭，仍保留统一 Runtime 装配与懒连接：自动 Memory 召回和写入由 `MemoryManager` 的开关短路，自动 RAG 召回由 `RagPipeline.recall()` 短路（`src/myagent/memory/manager.py:136`、`src/myagent/memory/manager.py:172`、`src/myagent/rag/pipeline.py:248`）。

RAG 自动召回遇到 Embedding / Qdrant 错误时记录 warning 并返回空上下文；显式 `retrieve()` 继续抛错供命令行诊断（`src/myagent/rag/pipeline.py:240`、`src/myagent/rag/pipeline.py:263`）。网页设置先校验 LLM 与可选配置，再写 `.env` 并重建 Agent，Key 仅以掩码形式返回浏览器（`src/myagent/gateway/config.py:108`、`src/myagent/gateway/config.py:138`、`src/myagent/gateway/app.py:131`）。

## 验证

- 无 Embedding Key、无 Qdrant 服务时的 Agent 对话通过离线假件验证，向量调用被断言不可发生（`tests/test_runtime.py:53`）。
- Memory 和 RAG 默认值、关闭时短路，以及 RAG 自动故障降级与显式检索报错均有测试（`tests/test_settings.py:404`、`tests/test_memory.py:1349`、`tests/rag/test_pipeline.py:303`、`tests/rag/test_pipeline.py:316`）。
- 网页配置验证掩码、字段校验、保存与运行时重载，以及无效编辑不写盘（`tests/gateway/test_config.py:203`、`tests/gateway/test_app.py:282`、`tests/gateway/test_app.py:318`）。
- 质量门：`scripts/check.sh` 全绿（809 passed，`src/myagent` 语句与分支覆盖率 100%）；`.venv/bin/python scripts/check_doc_anchors.py` 全绿（1361 个锚点）。

## 限制

保存设置时只校验 URL 和字段形状，不探测 Qdrant 网络连通性；实际连接在功能调用时进行。自动 RAG 召回失败会跳过本轮上下文，显式搜索仍报告错误（`src/myagent/rag/pipeline.py:248`）。
