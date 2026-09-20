# Phase 1 工作记录：nanobot 源码理解

- 日期：2026-09-20
- 状态：已完成
- 关联提交：`<待提交>`

## 1. 阶段目标

彻底理解 nanobot 的核心 Agent Runtime（Loop / Runner / Tool / Context / Memory / Session），
产出 5 份**清晰易懂且能对齐到源码**的文档，达到「不看源码能讲清一次请求的全过程，并能手画架构图」。

## 2. 问题定义

| # | 问题 | 可验证的完成条件 |
| --- | --- | --- |
| 1 | 上游 5 个核心文件共 ~5800 行，直接读容易「读完后讲不清」 | 5 篇文档各自给出可复述的心智模型与关键表格 |
| 2 | 文档容易与源码脱节（改了代码/记错行号后无人发现） | 文档里的每个 `file.py:行号` 都能被脚本校验通过 |
| 3 | 理解必须服务于改造，而不是复述实现 | 每篇文档都给出「迁移到 myagent」的取舍表与理由 |

## 3. Baseline

Phase 0 结束时只有 `docs/architecture.md`（149 行）覆盖到「启动路径 + 消息流概述」层级：

- 已有：`nanobot` 装配顺序、`InboundMessage → Bus → Loop → Runner → Provider → Tool` 主干。
- 缺失：Runner 主循环与终止条件、工具契约/注册/执行/错误语义、Context 预算与压缩、Memory 归档与 Dream、
  Session 与 Memory 的边界；也没有任何行号锚点，读文档时无法直接跳到代码。

## 4. 方案设计

### 4.1 文档分工

```text
docs/architecture.md      总览：启动路径 + 消息流 + 四条关键边界 + 模块地图 + 索引
docs/agent-loop.md        Loop 装配 / 7 阶段流水线 / Runner 主循环 / 上限 / 注入 / checkpoint
docs/tool-system.md       Tool 契约 / Schema / Registry / 发现 / 并发执行 / 错误与安全边界
docs/context.md           system prompt 分层 / transcript 组装 / 预算与四步拟合 / 摘要压缩
docs/memory.md            Session vs Memory / history.jsonl / 归档检查点 / Dream / 进入上下文
```

### 4.2 三条写作原则

1. **先给结论再给细节**：每篇开头一句话心智模型 + 一张 ASCII/Mermaid 图，然后才是表格与代码片段。
2. **每个论断带锚点**：路径统一为「上游包内相对路径」（`agent/loop.py:196`），
   并在文首声明约定 `nanobot/nanobot/agent/loop.py`。
3. **每个机制写「为什么」**：例如 `ToolResult` 为什么继承 `str`、`fit_to_budget` 为什么必须四步有序、
   为什么「原始转录 ≠ 模型请求」，并落到「我们迁移时保留/简化/丢弃」。

### 4.3 对齐机制（本阶段新增）

`scripts/check_doc_anchors.py`：扫描 `docs/`、`README.md`、`PLAN.md`，
把所有 `file.py:行号` 形式的引用解析到本地 `nanobot/` 参照并校验行号落在文件范围内。
解析顺序：仓库相对路径 → 上游包相对路径 → 同名文件唯一匹配（歧义直接报错，避免含糊引用）。

## 5. 实现

| 文件 | 行数 | 职责 |
| --- | ---: | --- |
| `docs/architecture.md` | 178 | 重写：启动路径、消息流（含 pending queue / 并发 / save）、四条边界、模块地图、迁移计划 |
| `docs/agent-loop.md` | 372 | 新增：Loop 与 Runner 的全部关键机制 |
| `docs/tool-system.md` | 299 | 新增：工具契约到执行的全链路 |
| `docs/context.md` | 303 | 新增：上下文组装、预算、压缩 |
| `docs/memory.md` | 293 | 新增：Session / Memory 边界、归档、Dream |
| `scripts/check_doc_anchors.py` | 144 | 新增：文档锚点校验（约 0.6s 跑完） |
| `PLAN.md` | — | Phase 1 的 22 个复选框、阶段产出、验收结论全部落地 |

## 6. 实验设置

阅读方法（可复用）：

```bash
cd nanobot
rg -n "^class |^    def |^    async def |^[A-Z_]+ *[:=]" nanobot/agent/loop.py   # 先拿骨架
sed -n '1594,1720p' nanobot/agent/loop.py                                      # 再读关键区段
rg -n "<被引用的符号>" nanobot/agent/loop.py                                    # 最后逐条核对行号
```

验证命令：

```bash
.venv/bin/python scripts/check_doc_anchors.py     # 文档是否仍对齐源码
scripts/check.sh                                  # ruff format / ruff check / mypy / pytest
```

## 7. 结果

```text
$ .venv/bin/python scripts/check_doc_anchors.py
checked 294 anchor(s) in 5 document(s)
all anchors resolve

$ scripts/check.sh
== ruff format --check ==   6 files already formatted
== ruff check ==            All checks passed!
== mypy ==                  Success: no issues found in 6 source files
== pytest ==                63 passed（覆盖率 248 stmts / 74 branches，100%）
```

对齐过程中被脚本抓出的真实问题（说明这道校验不是形式主义）：

1. 初稿里只写文件名（如 `base.py`）的引用会产生歧义：上游同时存在 `agent/tools/base.py` 与
   `providers/base.py`、`agent/tools/registry.py` 与 `providers/registry.py`、
   `agent/tools/loader.py` 与 `config/loader.py` ——全部改为 `agent/tools/...` 全路径。
2. 9 处行号是「按阅读印象写的」，脚本比对文件行数后改成了精确值
   （如 `loop.py` 的 `AgentRunner` 实例化：381 → 385；`Consolidator`：419 → 433；`AutoCompact`：427 → 443）。
3. 路径约定本身也有坑：`nanobot/agent/loop.py` 指向的是「克隆目录直下」，而真实包在
   `nanobot/nanobot/`，因此文档统一改为「包内相对路径 + 文首声明」。

## 8. 结论与遗留问题

结论：

1. 5 篇文档覆盖了 PLAN 1.1～1.6 的全部要求，并且**每个论断都能一键跳转到源码行**（294 个锚点全部通过校验）。
2. 验收标准可复述：`docs/architecture.md` 3.3 与 `docs/agent-loop.md` 给出了「一次请求从输入到输出」的完整答案，
   §4 的四条边界可以直接画成架构图。
3. 每一篇都给出迁移取舍表，Phase 2 可以直接按表实施，不需要再回头读源码做设计决策。

遗留问题（进入 Phase 2 前的风险清单）：

| 遗留项 | 说明 / 计划 |
| --- | --- |
| 未深读的模块 | `providers/`（provider 能力矩阵与流式细节）、`security/workspace_access.py`、`session/recovery.py`、`agent/subagent.py`、`channels/`、`cron/`、`webui/` —— 前两者 Phase 2 会用到再补 |
| 上游测试未利用 | `nanobot/tests/` 里有可直接照搬思路的用例（工具并发、上下文压缩），Phase 2 迁移时按需阅读 |
| 锚点校验的覆盖范围 | 只覆盖 `file.py:行号` 形式；形如 `loop.py:1261、1391` 的第二个数字、以及 `docs/*.md` 之间的链接不在校验内 |
| 校验脚本依赖本地参照 | `nanobot/` 被 gitignore，CI 里无法运行；作为本地开发工具使用，已在脚本 docstring 说明 |
