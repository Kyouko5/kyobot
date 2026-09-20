# AGENTS.md

面向在本仓库工作的 coding agent 的约定。与 `docs/development.md` 一致时以 `docs/development.md` 为准。

## 提交（Git）

- **每个阶段（PLAN 的一个 Phase）完成后自动提交，不需要询问用户**；阶段内的每个 Task 也可以
  单独提交。**不要**把多个阶段的改动混在一个提交里。
- 提交信息用 Conventional Commits，标题用英文，例如：
  `feat(phase4): implement the layered memory system`、
  `docs(phase4): document the layered memory design, decisions and experiments`、
  `chore(scripts): add the phase 4 memory experiment harness`。
- 提交前必须让质量门是绿的（见下），不要把失败的红灯提交进去。
- 只 `git add` 本阶段相关的文件；不要提交 `.env`、`data/`、`nanobot/`（已在 `.gitignore` 中）。
- 不创建分支、不改写历史、不 `git push`，除非用户明确要求。

## 质量门（提交前必跑）

```bash
scripts/check.sh                          # ruff format --check → ruff check → mypy --strict → pytest --cov
.venv/bin/python scripts/check_doc_anchors.py
```

- 覆盖率要求 **100%**（`src/myagent` 的语句与分支）。
- 文档里的 `file.py:行号` 锚点必须全部解析；引用本仓库代码要写全路径
  （`src/myagent/memory/store.py:12`），裸文件名会被解析到上游 `nanobot/`。

## 代码与文档

- 源码在 `src/myagent/`，测试在 `tests/`（默认离线：用 `tests/fakes.py` 的假件，不打网络），
  文档在 `docs/`（`docs/decision-records/` 记决策、`docs/records/` 记阶段工作记录）。
- `nanobot/` 是上游只读参照，不参与构建，不要修改。
- 文档用中文，先结论后细节，每条论断都带可校验的锚点。
- 新增阶段产出时同步更新 `README.md`（进度表 / 目录树 / 文档索引）与 `PLAN.md` 的复选框和结论。
