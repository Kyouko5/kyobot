# Phase 7 工作记录：Research Agent

- 日期：2026-09-27
- 状态：已完成离线验收

## 结论

四个要求场景与拒答均已通过可重复的离线端到端演示。真实 PDF 文本层、SQLite、
Qdrant 本地模式、应用工具及通用 Agent Loop 均参与运行；模型响应和 Embedding
是确定性替身，因此下表验证的是编排、范围、引用回跳与记忆跨会话，而非在线模型的
事实正确率（`examples/research_agent/offline_demo.py:50`、
`examples/research_agent/offline_demo.py:66`、`examples/research_agent/offline_demo.py:142`）。

## 实现与边界

应用的可安装入口为 `src/myagent/research/agent.py:93`，将五个工具、研究提示词、
同一组 RAG/Memory/Session 实例交给 `build_agent()`。工具在
`src/myagent/research/tools.py:310` 注册，泛用 Loop 没有修改。`PaperLibrary` 用
解析后的路径和 PDF 后缀限定论文集合，`search_paper` 将 ID 集合传给检索器，
`read_paper` 还校验 PDF 与摄取时的哈希是否一致
（`src/myagent/research/tools.py:34`、`src/myagent/research/tools.py:143`、
`src/myagent/research/tools.py:193`）。

CLI 加入 `research ingest/ask/chat` 和 `docs show`：前者检查文件位于配置的论文根目录，
`ask --paper` 为工具和自动召回设同一范围；后者直接从 SQLite 展示引用块与页码
（`src/myagent/research/cli.py:28`、`src/myagent/research/cli.py:74`、
`src/myagent/cli.py:286`）。应用说明见 [`docs/research-agent.md`](../research-agent.md)。

## 运行方式与计量

```bash
PYTHONPATH=src .venv/bin/python examples/research_agent/offline_demo.py --json
```

运行使用仓库内两篇两页的合成 PDF；`graph_indexing.pdf` 的 ID 是
`5f16da93bfaf16fb`，`dense_retrieval.pdf` 的 ID 是 `fdbeb499dbe01dec`。
脚本将每个场景的断言与结果一起运行，失败会以非零退出
（`examples/research_agent/offline_demo.py:142`、
`examples/research_agent/offline_demo.py:317`）。以下数字为 2026-09-27 本机一次运行结果：

| 场景 / turn | 模型轮数 | 工具调用 | 估算 token | 延迟 ms |
| --- | ---: | ---: | ---: | ---: |
| 单论文 1：索引对象 | 2 | 1 | 1225 | 9.47 |
| 单论文 2：改进点 | 2 | 1 | 1404 | 11.47 |
| 单论文 3：代价 | 2 | 1 | 1583 | 7.26 |
| 多论文比较 | 2 | 2 | 1530 | 8.33 |
| 跨 Session A：记偏好 | 2 | 1 | 1328 | 9.24 |
| 跨 Session B：读偏好 | 1 | 0 | 697 | 6.12 |
| 检索 + 笔记 + 记忆 | 4 | 3 | 3449 | 11.92 |
| 无证据拒答 | 2 | 1 | 1660 | 8.69 |

模型轮数来自 `ScriptedModel.generate` 的调用次数，工具调用数来自本轮 tool 消息，
token 是每次请求及响应的本地估算之和，延迟是 `run_turn` 的本机经过时间；
它们不是提供方返回的 usage 或在线服务时延
（`examples/research_agent/offline_demo.py:66`、
`examples/research_agent/offline_demo.py:121`）。

## Transcript 摘录与判定

**Demo 1，单论文连续追问。** 三问使用同一 `research:single` Session：

```text
What is indexed? → It indexes entities and relations [5f16da93bfaf16fb#0].
What improves?  → It improves multi-hop accuracy [5f16da93bfaf16fb#1].
What is the cost? → It adds indexing overhead [5f16da93bfaf16fb#1].
```

人工对照合成 PDF 的明示句子，三条内容正确，引用指向对应块。三轮分别调用
`search_paper`、`read_paper`、`search_paper`；脚本还断言答案包含正确引用
（`examples/research_agent/offline_demo.py:187`）。

**Demo 2，多论文比较。**

```text
Compare the methods
→ Graph uses traversal [5f16da93bfaf16fb#0]; dense uses vectors [fdbeb499dbe01dec#0].
```

脚本分别以两组 `document_ids` 调用检索，并断言两个工具结果不会互相包含对方的
文档 ID；答案各引用一篇，内容与合成 PDF 一致
（`examples/research_agent/offline_demo.py:228`、
`examples/research_agent/offline_demo.py:241`）。

**Demo 3，跨 Session。**

```text
research:a / Remember my preference → Saved your preference.
research:b / What is my preference? → You prefer concise graph comparisons.
```

Session A 的 `save_note` 写入语义记忆，Session B 的模型请求在自动 Memory section
读到完整偏好；脚本检查第二轮模型输入包含该文字，未复用 A 的对话历史
（`examples/research_agent/offline_demo.py:248`、
`examples/research_agent/offline_demo.py:269`）。

**Demo 4，组合工具。**

```text
Research and remember
→ search_paper → save_note → search_memory
→ Graph traversal supports multi-hop answers [5f16da93bfaf16fb#0].
```

脚本检查最后一轮工具结果再次包含写入的笔记，故三种工具均经过真实执行路径
（`examples/research_agent/offline_demo.py:271`、
`examples/research_agent/offline_demo.py:285`）。

**拒答。** 对不存在的未发表量子论文提问，模型先调用 `search_paper`，答复为
「知识库中没有这篇论文的证据，无法回答。」；提示词明确要求无证据时拒答
（`examples/research_agent/offline_demo.py:291`、
`src/myagent/research/prompts.py:3`）。向量搜索可能返回语义无关的近邻，
生产模型是否守住拒答规则仍需后续真实模型评测。

## 验证

离线测试覆盖五个工具的 schema、范围与错误语义、CLI 会话和引用回跳、四个场景及拒答
（`tests/examples/test_research_tools.py:1`、`tests/examples/test_research_cli.py:1`、
`tests/examples/test_research_agent.py:1`、`tests/examples/test_offline_demo.py:1`、
`tests/test_cli.py:1`）。`scripts/check.sh` 全绿：ruff format/check、mypy strict，
**849 passed**，`src/myagent` 语句与分支覆盖率均 **100%**。
`.venv/bin/python scripts/check_doc_anchors.py` 检查 1411 个锚点，全部可解析。
