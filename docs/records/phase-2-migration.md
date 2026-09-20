# Phase 2 工作记录：核心代码迁移（Framework V1）

- 日期：2026-09-20
- 状态：已完成
- 关联提交：`<待提交>`
- 关联文档：`docs/design.md`（设计）、`docs/decision-records/0006-phase2-dependencies.md`（依赖决策）

## 1. 阶段目标

不再改 nanobot，而是把它的核心 Runtime **迁移成自己的框架**：迁移设计思想与必要实现，
但重新划定模块边界（PLAN Phase 2 前言）。

产物定位：**Framework V1——能独立跑通，但不追求模块解耦**。
「把 `nanobot/` 移出仓库后仍然可用」是本阶段唯一不可让步的验收项。

## 2. 问题定义

| # | 问题 | 可验证的完成条件 |
| --- | --- | --- |
| 1 | 上游 Runtime 有 7 阶段 + 多频道 + 崩溃恢复，直接照搬会把 V1 拖进工程化细节 | 只保留 4 阶段，删除的能力在 `docs/design.md` §5 列成 Phase 3+ 的输入 |
| 2 | 「迁移」容易变成「复制」，看不出重新设计的部分 | `docs/design.md` §4 逐条给出「保留 / 简化 / 加法」与理由 |
| 3 | 框架是否真的独立，最容易自欺 | 验收时把 `nanobot/` 改名移走，真实跑一次「对话 + 工具调用」 |
| 4 | 没有真实运行痕迹的「已完成」不可信 | 本文件 §7 附真实 transcript（含工具调用与落盘记录） |

## 3. Baseline

Phase 1 结束时（`docs/records/phase-1-source-reading.md`）：

- 只有 `docs/` 里的 5 篇源码理解文档 + `src/myagent/{config,observability}` 两个模块；
- `src/myagent/` 里**没有任何 Agent Runtime**：没有 Loop、Runner、Tool、Model、Session；
- 质量门 `scripts/check.sh` 覆盖 6 个源文件、63 项测试、100% 覆盖率。

## 4. 方案设计

### 4.1 分层与依赖方向

```text
cli ──▶ agent.loop ──▶ agent.runner ──▶ models.base(Protocol) / tools.registry
                   ├─▶ agent.context
                   └─▶ session.manager ──▶ agent.types
agent 不 import models；runner 不 import openai        （理由见 docs/design.md §1）
```

### 4.2 关键取舍（与 Phase 1 迁移表的对应）

| 决策 | 来源 | 结果 |
| --- | --- | --- |
| Loop / Runner 分离 | `docs/agent-loop.md` §3「保留」 | `src/myagent/agent/loop.py:119` / `src/myagent/agent/runner.py:87` |
| 7 阶段简化为 4 阶段 | `docs/agent-loop.md` §3「阶段先简化」 | `restore`/`compact` 并入 `build`，命令交给 CLI |
| 工具失败不抛异常，回灌成观察 | `docs/tool-system.md` §5 | `src/myagent/tools/registry.py:153` 统一追加 retry hint |
| 只读工具并发分批 | `docs/tool-system.md` §5 | `src/myagent/agent/runner.py:220` |
| 会话 JSONL 追加式 + `last_archived` 预留 | `docs/memory.md` §5 | `src/myagent/session/manager.py:115`（Phase 3 后由 `src/myagent/session/base.py:44` 的契约描述） |
| Context 只做「原料」不做预算 | `docs/context.md` §4 | `src/myagent/agent/context.py:114`（Phase 3 已改为 section + 预算检查，裁剪留给 Phase 6） |
| Memory 先立接口不接线 | `docs/memory.md` §5 | `src/myagent/memory/base.py:30`（Phase 4 接线） |

### 4.3 新增依赖

`openai>=1.50`（唯一新增运行时依赖）与标准库 `argparse` 的 CLI，
理由与备选方案见 `docs/decision-records/0006-phase2-dependencies.md`。

## 5. 实现

> 下表是本阶段结束时的**快照**。Phase 3 重写/拆分了其中几个文件
> （`agent/context.py` 重写、`session/manager.py` 改名并拆出契约、`cli.py` 去掉装配），
> 现状见 `docs/records/phase-3-refactor.md` §5。

| 文件 | 行数 | 职责 |
| --- | ---: | --- |
| `src/myagent/agent/types.py` | 192 | `Message` / `Usage` / `StopReason` / `ToolCallRequest` / 总线事件 |
| `src/myagent/agent/runner.py` | 241 | 模型↔工具循环：迭代上限、超时、截断、分批、终止原因 |
| `src/myagent/agent/loop.py` | 221 | 4 阶段流水线、会话锁、最小 `MessageBus` |
| `src/myagent/agent/context.py` | 69 | 系统提示 + 历史 + 当前消息 |
| `src/myagent/models/base.py` | 103 | `BaseModel` 协议、`LLMResponse`、错误语义 |
| `src/myagent/models/openai_compat.py` | 213 | OpenAI 兼容实现与错误翻译 |
| `src/myagent/tools/base.py` | 255 | 工具契约、JSON Schema 校验与类型纠正 |
| `src/myagent/tools/registry.py` | 153 | 注册表：定义排序、`prepare_call`、`execute` |
| `src/myagent/tools/builtin/` | 6 文件 380 | 4 个工具 + `paths` 路径校验 + `build_default_registry` |
| `src/myagent/session/manager.py` | 163 | JSONL 会话存储与回放 |
| `src/myagent/memory/` | 3 文件 143 | V1 记忆接口 + 文件实现（未接线） |
| `src/myagent/cli.py` | 132 | `chat` / `tools` 子命令与装配 |
| `src/myagent/config/settings.py` | +205 | `LLMSettings` / `AgentSettings` 与各解析函数 |
| `tests/` | 15 文件 2952 | 新增 195 项测试（含假模型客户端、假 SDK 客户端） |

## 6. 实验设置

复现命令：

```bash
# 1) 依赖（.env 里已有可用的 LLM_BASE_URL / LLM_MODEL / LLM_API_KEY）
scripts/bootstrap.sh

# 2) 质量门
scripts/check.sh
.venv/bin/python scripts/check_doc_anchors.py

# 3) 离线命令（不需要任何凭据）
.venv/bin/myagent tools

# 4) 真实对话（含工具调用）
.venv/bin/myagent chat -m "现在几点了？用 calculator 算一下 (12+8)*3，再读一下 workspace/project-notes.md"
.venv/bin/myagent chat -m "把刚才算出来的那个结果再乘以 2，用 calculator 算"   # 复用同一会话
```

独立性验收（PLAN Phase 2 验收标准第一条）——把上游参照整个移走后再跑：

```bash
mv nanobot nanobot.moved
.venv/bin/myagent chat -m "帮我算一下 (12+8)*3，再读一下 workspace/project-notes.md"
mv nanobot.moved nanobot
```

## 7. 结果

### 7.1 `myagent tools`（无凭据可用）

```text
$ .venv/bin/myagent tools
calculator (read-only)
    Evaluate a basic arithmetic expression with + - * / // % ** and parentheses.
    parameters: expression
current_time (read-only)
    Return the current date and time as an ISO-8601 timestamp.
    parameters: timezone
read_file (read-only)
    Read a text file from the workspace and return its lines with line numbers.
    parameters: path, max_lines
search_local (read-only)
    Search the workspace files for a literal string and return 'file:line: text' hits.
    This is a plain text scan, not a semantic search.
    parameters: query, limit
```

### 7.2 真实 transcript（`nanobot/` 已移出仓库）

第一条消息（`MYAGENT_LOG_LEVEL=DEBUG`，可以看到 4 个阶段的耗时；模型一次并行调用了 3 个只读工具）：

```text
$ MYAGENT_LOG_LEVEL=DEBUG .venv/bin/myagent chat -m "现在几点了？用 calculator 算一下 (12+8)*3，再读一下 workspace/project-notes.md"
stderr:
2026-09-20T13:33:03+0800 DEBUG   myagent.agent.loop - [turn 0009f166] stage build completed in 0.2ms
2026-09-20T13:33:07+0800 DEBUG   myagent.agent.loop - [turn 0009f166] stage run completed in 4151.4ms
2026-09-20T13:33:07+0800 DEBUG   myagent.agent.loop - [turn 0009f166] stage save completed in 1.2ms
2026-09-20T13:33:07+0800 DEBUG   myagent.agent.loop - [turn 0009f166] stage respond completed in 0.0ms
stdout:
现在是 **2026-09-20 13:33（+08:00）**。

- `(12+8)*3 = 60`
- `workspace/project-notes.md`（5 行，标题「论文助手项目笔记」）：
  - 目标：基于 nanobot 的设计实现个人 Agent Framework（Phase 2 完成核心迁移）
  - 检索语料：workspace/papers/（Phase 5 引入 RAG 后接入）
  - 待办：把 calculator / read_file 两个工具接进垂直 Agent 的评测用例

注：你给的是 `workspace/project-notes.md`，我按工作区根目录相对路径 `project-notes.md` 读取的。
```

第二条消息（同一会话，验证历史复用——模型知道「刚才的结果」是 60）：

```text
$ .venv/bin/myagent chat -m "把刚才算出来的那个结果再乘以 2，用 calculator 算"
60 × 2 = **120**。
```

落盘结果（`data/sessions/cli%3Adefault.jsonl`，`⏎` 表示换行）：

```text
 1: HEADER key=cli:default created=2026-09-20T05:33:03+00:00 last_archived=0
 2: user      现在几点了？用 calculator 算一下 (12+8)*3，再读一下 workspace/project-notes.md
 3: assistant current_time({"timezone": "Asia/Shanghai"}),calculator({"expression": "(12+8)*3"}),read_file({"path": "project-notes.md"})
 4: tool      2026-09-20T13:33:04+08:00
 5: tool      (12+8)*3 = 60
 6: tool      project-notes.md (5 lines)⏎   1 | # 论文助手项目笔记⏎ ...
 7: assistant 现在是 **2026-09-20 13:33（+08:00）**。⏎⏎- `(12+8)*3 = 60`⏎ ...
 8: user      把刚才算出来的那个结果再乘以 2，用 calculator 算
 9: assistant calculator({"expression": "60*2"})
10: tool      60*2 = 120
11: assistant 60 × 2 = **120**。
```

三点可以从落盘结构直接读出来：

1. **系统提示没有落盘**：第 1 行是会话头，第 2 行就是用户消息（`transcript_start` 的作用）。
2. **第二轮没有重复写入第一轮历史**：第 8 行紧接第 7 行（这正是 §8.1 修掉的 bug）。
3. **工具调用是「一次模型请求 → 三个工具 → 一次回答」**：第 3 行一条 assistant 消息带 3 个
   `tool_calls`，第 4～6 行是三条 `tool` 观察（只读工具并成一批，`src/myagent/agent/runner.py:220`）。

### 7.3 质量门

```text
$ scripts/check.sh
== ruff format --check ==   46 files already formatted
== ruff check ==            All checks passed!
== mypy ==                  Success: no issues found in 30 source files
== pytest ==                258 passed（覆盖率 1499 stmts / 398 branches，100%）

$ .venv/bin/python scripts/check_doc_anchors.py
checked 494 anchor(s) in 11 document(s)
all anchors resolve
```

Phase 0 基线是 248 stmts / 63 项测试、100% 覆盖率；本阶段把语句数翻了 6 倍，
覆盖率**不低于**基线（仍为 100%）。

### 7.4 验收判定（PLAN Phase 2「验收标准」）

| 判定项 | 结果 |
| --- | --- |
| 把 `nanobot/` 移出仓库后 `myagent chat -m "..."` 仍能完成一次对话 + 一次工具调用 | ✅ §7.2（工具调用可见于落盘的第 3～6 行） |
| `scripts/check.sh` 全绿（ruff / mypy / pytest），覆盖率不低于 Phase 0 基线 | ✅ §7.3 |
| 真实 transcript 写进 `docs/records/phase-2-migration.md` | ✅ §7.2 |

## 8. 过程中发现并修掉的两个真实问题

### 8.1 会话重复落盘（Phase 2 自己的 bug）

`_save_turn` 最初写的是 `bundle.messages[transcript_start:]`，而 `transcript_start` 当时等于 1
（「第一条属于对话的消息」）。第一轮历史为空，看不出问题；第二轮把**已经落盘的历史又写了一遍**，
落盘文件从 11 行涨到 17 行（重复的用户消息、工具调用与回答）。

修复：

- `ContextBundle.transcript_start` 的语义改成「本轮新增消息的起点」
  （`src/myagent/agent/context.py:149`、`:159`），历史越长它越大；
- 新增回归测试 `tests/test_loop.py::test_a_second_turn_does_not_store_the_history_twice`，
  同时断言内存与「重新从磁盘加载」两条路径。

教训写进设计：**「只存新增」这类不变量必须有跨轮次的测试**，单轮测试天然看不见它。

### 8.2 测试之间泄漏真实 `.env`

`tests/test_cli.py` 会执行 `myagent tools`，而 CLI 启动时会调用 `configure_logging()` 与
`AgentSettings.from_env()`；`load_dotenv()` 直接把项目 `.env` 写进 `os.environ`，
于是**真实凭据与日志级别泄漏到后续测试**，`tests/test_env.py` / `tests/test_logging.py` 开始随机失败。

修复（两处，都在测试侧）：

- `tests/conftest.py` 增加 autouse 的 `isolated_env_file`：把 `MYAGENT_ENV_FILE` 指向一个空文件，
  框架的加载路径不变但内容为空；
- 把 `myagent_logger` 也改成 autouse：`configure_logging()` 改的是进程级状态，
  任何跑过 CLI 的测试都会给它留下 handler。

这两条都属于「框架代码没问题、测试卫生有问题」，但对 CI 的稳定性是必要的。

## 9. 结论与遗留问题

结论：

1. PLAN Phase 2 的 2.1～2.6 全部落地，4 个复选框组、阶段产出与验收标准均已满足。
2. 「独立可运行」不是声明而是证据：验收时 `nanobot/` 被移出仓库，两次真实对话（含 4 次工具调用）通过。
3. 迁移是「有取舍的迁移」：§4 的差异表让每一处简化/加法都能在评审里讲清理由。

遗留问题（进入 Phase 3 的输入）：

| 遗留项 | 说明 / 计划 |
| --- | --- |
| 装配仍在 `cli.build_agent_loop()` | Phase 3（PLAN 3.5）抽成 `runtime.py`，并用 Protocol 统一扩展点 |
| Loop 直接持有具体对象 | 依赖是构造注入但类型是具体类；Phase 3 §3.2 解耦 |
| `count_tokens()` 恒返回 `None` | Phase 6 的上下文预算需要真实 token 估算 |
| `memory/` 未接线 | Phase 4 的三层记忆与检索 |
| `search_local` 是桩 | Phase 7 换成真实论文检索 |
| `stream()` 未实现 | 保留接口；Phase 6 让 CLI 流式输出 |
| `default` 会话只有一个 | 多会话/会话列表属于 CLI 体验，Phase 3 之后按需补 |
| 模型调用失败的那一轮会留下「无回答的用户消息」 | 有意为之：失败也走完 save/respond，保证「用户说过什么」不丢（`docs/design.md` §3.7）；重试与补偿留给 Phase 9 |
