# Research Agent 示例

## 结论

本示例把论文检索、按页阅读和笔记写入接在通用 `build_agent()` 上；应用装配在
`src/myagent/research/agent.py:93`，五个工具在 `src/myagent/research/tools.py:310` 注册，
通用 Agent Loop 无需改动。`agent.py`、`tools.py`、`prompts.py` 是面向示例读者的导出入口，
CLI 可安装代码位于 `src/myagent/research/`。

## 离线运行四个 Demo

```bash
PYTHONPATH=src .venv/bin/python examples/research_agent/offline_demo.py
PYTHONPATH=src .venv/bin/python examples/research_agent/offline_demo.py --json
```

脚本使用 `fixtures/` 的两份两页 PDF，真实 PDF Loader、SQLite、Qdrant 本地模式、
Research Agent Loop 和五个工具；模型调用和 Embedding 用可重复的本地实现，
不会访问外部服务（`examples/research_agent/offline_demo.py:50`、
`examples/research_agent/offline_demo.py:142`）。四个场景与拒答均有断言；
输出的 token 是本地估算，延迟仅代表本机离线运行（`examples/research_agent/offline_demo.py:66`、
`examples/research_agent/offline_demo.py:121`）。完整 transcript 与数据见
[`docs/records/phase-7-research-agent.md`](../../docs/records/phase-7-research-agent.md)。

## 用自己的论文

```bash
cp .env.example .env
# 填写 LLM_*、EMBED_*、MYAGENT_QDRANT_URL；启动对应 Qdrant 服务
# 默认论文目录 data/papers，可用 MYAGENT_PAPERS_DIR 调整
mkdir -p data/papers
cp /path/to/paper.pdf data/papers/
myagent research ingest data/papers/*.pdf
myagent docs list
myagent research ask "这篇论文的方法是什么？" --paper <document_id>
myagent research chat --session work
myagent docs show <document_id> --chunk 0
```

`research ingest` 只接受根目录内 PDF；`--paper` 同时限制工具检索与自动 RAG 上下文
（`src/myagent/research/cli.py:74`、`src/myagent/research/agent.py:71`）。
显式 `search_paper` 需要 Embedding 和 Qdrant；跨会话记忆另需
`MYAGENT_MEMORY_ENABLED=true`；`MYAGENT_RAG_ENABLED=true` 控制自动文档召回
（`src/myagent/research/tools.py:111`、`src/myagent/research/tools.py:245`、
`src/myagent/research/agent.py:130`）。配置和证据规则见
[`docs/research-agent.md`](../../docs/research-agent.md)。
