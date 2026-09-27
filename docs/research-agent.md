# Research Agent：论文知识库问答

## 结论

Research Agent 只为应用增加论文工具、领域提示词和文档范围配置；对话仍由
`build_agent()` 装配和通用 Agent Loop 执行（`src/myagent/research/agent.py:93`、
`src/myagent/runtime.py:41`）。同一份 PDF 可多轮追问，多篇 PDF 可按 ID 过滤后比较，
重要偏好可写入跨 Session 的 SemanticMemory。离线验收的四个 Demo 与拒答记录在
[`docs/records/phase-7-research-agent.md`](./records/phase-7-research-agent.md)。

## 配置与操作

基础配置为 `LLM_BASE_URL`、`LLM_MODEL`、`LLM_API_KEY`；研究论文的摄取和显式搜索还需
`EMBED_MODEL_TYPE`、`EMBED_MODEL_NAME`、`EMBED_API_KEY` 与可访问的
`MYAGENT_QDRANT_URL`。可单独设置 `MYAGENT_MEMORY_ENABLED=true` 以保存及召回笔记，
设置 `MYAGENT_RAG_ENABLED=true` 以在每轮自动注入论文片段；两个开关默认关闭，
不会阻止普通 Agent 使用（`src/myagent/research/cli.py:44`、
`src/myagent/research/agent.py:71`、`src/myagent/research/tools.py:245`）。

```bash
mkdir -p data/papers
cp /path/to/paper.pdf data/papers/
myagent research ingest data/papers/*.pdf
myagent docs list
myagent research ask "这篇论文的主要结论是什么？" --paper <document_id>
myagent research chat --session work
myagent docs show <document_id> --chunk 0
```

`MYAGENT_PAPERS_DIR` 可替换默认的 `data/papers`。摄取只接收该根目录内的 PDF，
已入库但来源在目录外的文件也不会进入 Agent 视野；按 `--paper` 提问时，
该范围同时约束检索工具和自动文档召回（`src/myagent/research/cli.py:74`、
`src/myagent/research/tools.py:34`、`src/myagent/research/agent.py:71`）。
`--session` 复用同一会话；不同名称产生独立 JSONL，但启用 Memory 时共用长期记忆
（`src/myagent/research/cli.py:67`、`src/myagent/research/agent.py:110`）。

## 证据路径

| 工具 | 用途与边界 | 实现 |
| --- | --- | --- |
| `list_papers` | 显示目录内已入库 PDF 的 ID、标题、页数、块数 | `src/myagent/research/tools.py:72` |
| `search_paper` | 以 `document_ids` 过滤向量命中，返回 `[doc#idx]` 和原文片段 | `src/myagent/research/tools.py:111` |
| `read_paper` | 根据 ID 和页范围读取 PDF 原文；检查来源路径与摄取时的 SHA | `src/myagent/research/tools.py:170` |
| `save_note` | 将模型选出的重要结论或偏好写成 `source=tool` 的 SemanticMemory | `src/myagent/research/tools.py:245` |
| `search_memory` | 检索跨 Session 的长期记忆 | `src/myagent/research/tools.py:284` |

系统提示词要求先检索再回答；证据不足时明确说「知识库中没有」；事实附上
`[document_id#chunk_index]`，比较两篇时先各自总结并各给引用；只保存值得长期保留的
信息（`src/myagent/research/prompts.py:3`）。模型可能违反提示词，当前引用和拒答的
离线验证是应用层演示，并非生产环境的事实核查保证。

`myagent docs show <document_id>` 从 SQLite 显示各块原文和页码，`--chunk` 只显示指定块；
因此答案中的 `[doc#idx]` 可以回跳定位，而无需再次连接 Qdrant
（`src/myagent/cli.py:286`）。`read_paper` 提供按页原文，块的引用取自与页范围
相交的已入库片段（`src/myagent/research/tools.py:193`）。

## 复现与限制

运行 `PYTHONPATH=src .venv/bin/python examples/research_agent/offline_demo.py --json` 可得到
四个场景、拒答的 transcript 和轮数、工具次数、估算 token、延迟。脚本使用真实 PDF
解析与本地 Qdrant，但用固定模型响应验证工具编排；它不测外部 LLM 的遵循率或
Embedding 的相关性（`examples/research_agent/offline_demo.py:142`）。PDF 需有可提取
文本层；图表/OCR、联网抓取和自动综述不在 Phase 7 范围
（`src/myagent/rag/loader.py:138`、`PLAN.md:1882`）。
