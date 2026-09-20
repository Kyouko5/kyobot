# Phase 3 工作记录：Agent Framework 重构（Framework V2）

- 日期：2026-09-20
- 状态：已完成
- 关联提交：`<待提交>`
- 关联文档：`docs/design.md`（设计）、`docs/decision-records/0007-framework-extension-points.md`（扩展点决策）

## 1. 阶段目标

把 Phase 2 的 Framework V1（「能独立跑通」）改造成 V2（「模块可替换、依赖可注入」）：

1. **依赖倒置**：`AgentLoop` / `AgentRunner` 只依赖契约，不依赖 OpenAI 客户端 / Qdrant / SQLite；
2. **职责收敛**：Loop 只做「取消息 → 执行 → 回写」，拼 prompt、检索记忆/文档都不在 Loop 里；
3. **扩展点显式**：新增模型、工具、记忆存储、检索器，只需实现一个 `Protocol` 并在装配处注册。

## 2. 问题定义

| # | 问题 | 可验证的完成条件 |
| --- | --- | --- |
| 1 | 「可替换」最容易停留在口号 | 用 AST 检查核心模块的 import：不出现 `openai` / `qdrant_client` / `sqlite3`，也不出现具体实现（`tests/test_contracts.py:435`） |
| 2 | 契约的形状容易定错（比如为了省事把 `Any` 塞进去） | 每个契约配一个**不继承任何东西**的假实现，用它跑通链路（`tests/test_contracts.py:305`、`:341`） |
| 3 | 上下文预算不能只写在文档里 | `ContextManager` 有 `priority` / `budget_tokens`，超预算时行为明确（V1 报错，Phase 6 裁剪） |
| 4 | 换实现要真的只改一处 | 换模型只改 `.env`（`tests/test_contracts.py:387`）；加工具只 `register`（`tests/test_contracts.py:322`） |

## 3. Baseline

Phase 2 结束时（`docs/records/phase-2-migration.md`）：

- 258 项测试、1499 stmts / 398 branches、100% 覆盖率，mypy strict 覆盖 30 个源文件；
- Loop 的依赖是构造注入，但**类型是具体类**：`OpenAICompatModel` / `ContextBuilder` /
  `SessionManager`（`docs/design.md` §4.4 的对照表）；
- 装配写在 `cli.build_agent_loop()`——Phase 2 记录里明确列为遗留项；
- `agent/context.py` 只有 `ContextBuilder`，拼字符串、没有预算概念；
- `memory/` 的接口是 Phase 2 的形状（`add(content)` / `search(query, limit=)`），且未接线；
- 质量门：`scripts/check.sh` 全绿，文档锚点 494 个全部解析。

## 4. 方案设计

### 4.1 三个动作

```text
① 契约化      具体类 → Protocol（6 个扩展点 + ContextManager + SessionStore）
② 职责收敛    ContextBuilder（拼字符串） → ContextManager（section + 优先级 + 预算）
              Loop 的构造函数只收契约，不 new 任何东西
③ 单一装配点  cli.build_agent_loop() → myagent.runtime.build_agent(settings)
```

依赖方向（粗体是本阶段建立的「只认契约」边界）：

```text
cli.py ──▶ runtime.py ──▶ 具体实现（OpenAICompatModel / builtin tools / JsonlSessionStore / SectionedContextManager）
             │
             └─▶ AgentLoop ──▶ **BaseModel** / **ContextManager** / **SessionStore** / ToolRegistry / AgentRuntimeConfig
```

### 4.2 关键取舍

| 决策 | 依据 | 结果 |
| --- | --- | --- |
| 契约用 `Protocol`，不强制继承 | ADR-0007 | `src/myagent/tools/base.py:152`（`BaseTool`）+ `:187`（`Tool(ABC)` 降为便利实现） |
| 契约就近定义，不建 `protocols.py` | ADR-0007 | `rag/`、`memory/` 各自持有契约（`src/myagent/rag/retriever.py:19`） |
| `ContextSection` 带优先级但本阶段不裁剪 | PLAN 3.3 | 超预算抛 `ContextBudgetExceeded`（`src/myagent/agent/context.py:148`） |
| 检索结果以 `ContextItem` 交给上下文 | `agent` 不能依赖 `memory` / `rag` | `src/myagent/agent/context.py:74` |
| 运行期上限独立成 `AgentRuntimeConfig` | PLAN 3.2 | 预算公式 `context_window - max_tokens - 1024`（`src/myagent/agent/runtime.py:73`） |
| 超预算不落盘、模型失败仍落盘 | 请求是否真的发出去 | `src/myagent/agent/loop.py:174`、`:197` |
| 新增 `tokens.py` | Phase 5/6 要用同一把尺子 | `src/myagent/tokens.py:37` |

## 5. 实现

| 文件 | 行数 | 职责 |
| --- | ---: | --- |
| `src/myagent/runtime.py` | 65 | **唯一装配点**：`build_agent(settings, *, 6 个可覆盖组件)` |
| `src/myagent/agent/runtime.py` | 78 | `AgentRuntimeConfig`：迭代上限 / 工具超时 / 截断阈值 / 上下文预算 |
| `src/myagent/agent/context.py` | 295 | `ContextSection` / `ContextRequest` / `ContextBundle` / `CompactionReport` / `ContextManager` / `SectionedContextManager` |
| `src/myagent/session/base.py` | 61 | `Session` / `SessionStore` 契约 / `DEFAULT_SESSION_KEY` |
| `src/myagent/session/manager.py` | 149 | `JsonlSessionStore`（Phase 2 的 `SessionManager` 改名并实现契约） |
| `src/myagent/rag/` | 5 文件 173 | `Document` / `Chunk` / `ScoredPoint` / `RetrievedChunk` + 三个契约（无 qdrant 依赖） |
| `src/myagent/tokens.py` | 49 | `estimate_tokens`：CJK 1 token/字，其余 4 字符/token |
| `src/myagent/agent/loop.py` | 263 | 4 阶段改为契约注入；`TurnContext.error`；超预算不发送 |
| `src/myagent/memory/base.py` | 105 | `MemoryRecord.create` + `BaseMemory`（`add(record)` / `search(kind=, top_k=)`） |
| `src/myagent/tools/base.py` | 300 | `BaseTool` Protocol + `Tool` ABC |
| `src/myagent/config/settings.py` | 412 | 新增 `Settings` 单一配置对象（`llm` / `agent` / `sqlite` / `qdrant` / `embedding`） |
| `src/myagent/cli.py` | 116 | 删除 `build_agent_loop`，只调用 `build_agent` |
| `tests/test_contracts.py` | 449 | 契约、边界与装配的测试（含 AST import 检查） |
| `tests/test_runtime.py` | 103 | 装配点与运行参数的测试 |
| `tests/test_context.py` / `tests/test_tokens.py` | 174 / 29 | section、预算、token 估算 |
| `docs/design.md` | 800 | V2 设计：职责表 / 契约 / 装配图 / 差异表 / 四个答辩问题 |
| `docs/decision-records/0007-framework-extension-points.md` | 87 | 扩展点决策与备选方案 |

`src/myagent/` 现在是 **39 个源文件 / 3910 行**（Phase 2 结束时 30 个文件 / 3112 行）。

## 6. 实验设置

```bash
# 1) 质量门
scripts/check.sh
.venv/bin/python scripts/check_doc_anchors.py

# 2) 离线命令（不需要凭据）
.venv/bin/myagent tools

# 3) 真实对话（走 .env 里的 LLM_BASE_URL / LLM_MODEL / LLM_API_KEY）
.venv/bin/myagent chat -s cli:phase3 -m "现在几点了？用 calculator 算一下 (12+8)*3，然后读一下 workspace/project-notes.md"
.venv/bin/myagent chat -s cli:phase3 -m "把刚才算出的结果再乘以 2，用 calculator 算"

# 4) 单看契约相关的测试
.venv/bin/python -m pytest tests/test_contracts.py -q
```

## 7. 结果

### 7.1 「可替换」是怎么被证明的

`tests/test_contracts.py` 用四类断言把「可替换」变成可执行的事实：

| 断言 | 位置 | 说明 |
| --- | --- | --- |
| 六个扩展点是结构类型 | `tests/test_contracts.py:252` | `isinstance(DuckTool(), BaseTool)` 等；假件 `__mro__` 里只有 `object`（`:266`） |
| 契约不全就不算满足契约 | `tests/test_contracts.py:274` | `ScriptedModel` 只有 `generate`，因此**不**是 `BaseModel`——`stream` / `count_tokens` 也是契约的一部分 |
| 假件能跑通整条链路 | `tests/test_contracts.py:305`、`:341` | 假模型 + 假工具 + 假 context + 假 session store，一次 `run_once` 正常返回 |
| 依赖方向 | `tests/test_contracts.py:435`、`:442`、`:446` | AST 检查核心模块不 import SDK / 具体实现，且具体实现只在 `myagent.runtime` 碰面 |

### 7.2 真实 transcript（`nanobot/` 未参与，`cli:phase3` 会话）

```text
 1: session   cli:phase3
 2: user      现在几点了？用 calculator 算一下 (12+8)*3，然后读一下 workspace/project-notes.md
 3: assistant tool_calls: current_time() / calculator({"expression": "(12+8)*3"}) / read_file({"path": "project-notes.md"})
 4: tool      2026-09-20T15:16:50+08:00
 5: tool      (12+8)*3 = 60
 6: tool      project-notes.md (5 lines)⏎   1 | # 论文助手项目笔记⏎ ...
 7: assistant 现在是 **2026-09-20 15:16:50（+08:00）**。⏎⏎- 计算结果：`(12+8)*3 = 60`⏎ ...
 8: user      把刚才算出的结果再乘以 2，用 calculator 算
 9: assistant calculator({"expression": "60*2"})
10: tool      60*2 = 120
11: assistant `60 * 2 = 120`⏎⏎（即 `(12+8)*3*2 = 120`）
```

三点可以直接从落盘结构读出来：

1. **系统块（含 section 合并后的 system 消息）没有落盘**：第 2 行就是用户消息，
   这正是 `transcript_start`（`src/myagent/agent/context.py:124`）的作用；
2. **第二轮只追加本轮消息**：第 8 行紧接第 7 行，历史没有被重写；
3. **只读工具仍然并成一批**：第 3 行一条 assistant 消息带 3 个 `tool_calls`，
   第 4～6 行是三条 `tool` 观察（`src/myagent/agent/runner.py:220`）——重构没有改变执行语义。

### 7.3 质量门

```text
$ scripts/check.sh
== ruff format --check ==   58 files already formatted
== ruff check ==            All checks passed!
== mypy ==                  Success: no issues found in 39 source files
== pytest ==                304 passed（覆盖率 1761 stmts / 434 branches，100%）

$ .venv/bin/python scripts/check_doc_anchors.py
checked 691 anchor(s) in 13 document(s)
all anchors resolve
```

Phase 2 基线是 258 项测试 / 1499 stmts；本阶段新增 46 项测试、262 条语句，覆盖率仍为 100%。

### 7.4 验收判定（PLAN Phase 3「验收标准」）

| 判定项 | 结果 |
| --- | --- |
| 换模型（DashScope → 本地 OpenAI 兼容端点）只改 `.env` | ✅ `tests/test_contracts.py:387`（两份 `Settings` → 两个 `OpenAICompatModel`，其余组件类型不变） |
| 新增一个工具只需 `registry.register(...)`，runner / loop 零改动 | ✅ `tests/test_contracts.py:322`（新工具的 schema 原样到达提供方） |
| `AgentRunner` 的 import 里不出现 `openai` / `qdrant_client` | ✅ `tests/test_contracts.py:435`（AST 检查 `openai` / `qdrant_client` / `sqlite3` / `httpx`） |
| `scripts/check.sh` 全绿，`tests/test_contracts.py` 覆盖六个 Protocol | ✅ §7.3、§7.1 |
| 四个答辩问题写进 `docs/design.md` 并指向上游锚点或本仓库代码 | ✅ `docs/design.md` §6.1～§6.4 |

## 8. 过程中发现并修掉的两个真实问题

### 8.1 质量门拦下的格式化与 lint 漂移

重构过程中新增的 8 个文件里，有 3 个没被格式化、5 处 lint 违规
（`tests/test_context.py` 的 import 未合并、`tests/test_contracts.py` 的 Yoda 条件、
`tests/test_tokens.py` 里三个「全角字符」触发的 `RUF001`）。

处理：`ruff format` + `ruff check --fix` 修掉前两类；`tests/test_tokens.py` 的两行加
`# noqa: RUF001` 并注明理由——**那些全角字符正是测试对象**（全角标点与全角字母应被算作 CJK），
不是笔误。教训：新增测试后先跑 `scripts/check.sh` 再写文档，否则文档里的行号会因为
`ruff format` 重排行而整体漂移（本次就是这么发现的）。

### 8.2 `myagent` 命令行入口失效（`.venv` 被 macOS 打了 `hidden` 标志）

**现象**：在普通终端里 `.venv/bin/myagent tools` 报 `ModuleNotFoundError: No module named 'myagent'`，
而 `PYTHONPATH=src .venv/bin/myagent tools` 正常。

**根因（实测）**：`.venv` 整棵树被 macOS 打上了 `hidden` 标志
（`ls -lO .venv/lib/python3.12/site-packages | grep -c hidden` → 93，`.venv/bin` → 30）。
被标记的 `_editable_impl_myagent.pth` 不会被 Python 的 `site` 模块读到，
于是 `pip install -e .` 写下的 `src` 路径没有进入 `sys.path`——`import myagent` 自然找不到包。
**文件内容本身是对的**：它的 sha256 与 `myagent-0.1.0.dist-info/RECORD` 里记的一致
（内容是 `/Users/kyouko/Desktop/2026FALL/kyobot/src`，41 字节）。

**修复**（在普通终端里执行一次即可）：

```bash
chflags -R nohidden .venv
.venv/bin/myagent tools        # 4 个内置工具又回来了
```

验证：`import myagent` 指向 `src/myagent/__init__.py`；
`myagent chat -s cli:phase3 -m "现在几点了"` 正常回答（还复用了上一轮的时间）。

**诊断中走过的弯路（值得记下）**：这次先在沙箱终端里排查，而沙箱对 `.venv` 的写入是叠加式的——
文件内容会落到真实文件，`chflags` 这类元数据改动不会。于是同一份文件在两边表现不同
（沙箱里「清掉标志就能跑」，真实终端里照旧失败），一度误判为「`.pth` 内容被改坏」。
最后以真实 shell 的实测 + `RECORD` 的 sha256 校验为准。

**兜底**：如果重装（`scripts/bootstrap.sh`）后仍然失败，用
`PYTHONPATH=src .venv/bin/myagent ...`（README 里本来就有 `PYTHONPATH=src python3 -m pytest` 这条等价路径）。

## 9. 结论与遗留问题

结论：

1. PLAN Phase 3 的 3.1～3.5、阶段产出与验收标准全部落地，四个答辩问题写进 `docs/design.md` §6；
2. 「模块可替换」不是声明而是可执行的断言：契约 → 假件 → AST import 检查三层，
   任何一次「核心 import 具体实现」都会让 `tests/test_contracts.py` 直接失败；
3. 装配收敛到 `myagent.runtime.build_agent()` 一处，Phase 2 遗留的
   「装配与命令行混在一个文件里」到此结束；
4. 重构没有改变运行时语义：真实对话里「一次模型请求 → 3 个只读工具并发 → 一次回答」
   与 Phase 2 记录 §7.2 的表现一致。

遗留问题（进入 Phase 4+ 的输入）：

| 遗留项 | 说明 / 计划 |
| --- | --- |
| 超预算只报错、不裁剪 | Phase 6 按 `ContextSection.priority` 裁剪；`required` section 放不下时才保留这个错误 |
| `compact()` 是空实现 | Phase 6 实现摘要压缩，并前移 `Session.last_archived`（`src/myagent/session/base.py:38`） |
| `ContextRequest.memories` / `.rag_chunks` 没有人填 | Phase 4 接 `BaseMemory`、Phase 5 接 `BaseRetriever`，都要在 `build_agent` 里加一行 |
| `BaseEmbedder` / `BaseVectorStore` / `BaseRetriever` 没有实现 | Phase 5 |
| `count_tokens()` 仍返回 `None` | Phase 6 用真实 tokenizer 或 provider 计数替换 `tokens.estimate_tokens` 的估算 |
| `stream()` 未实现，CLI 不流式 | Phase 6+ |
| `SessionStore` 只有 JSONL 实现 | ADR-0003 的 SQLite 会话/记忆存储属于 Phase 4 |
| 多会话管理（列表/切换） | `known_keys()` 已就位，CLI 只用默认会话；按需在 Phase 7 之后补 |
