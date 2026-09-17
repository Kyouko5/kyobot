# Repository Guidelines

## Project Structure & Module Organization

`kyobot` is a two-layer workspace built on the open-source `nanobot` agent framework. The roadmap lives in `docs_for_nano/nanobot_agent_project_plan.md`.

- `nanobot/` — Upstream baseline (Python 3.11+, MIT). The root repo ignores this path and it keeps its own remote history, so treat it as a read-only reference unless you are intentionally patching the fork.
- `my-agent-framework/` — Destination for new work. Planned layout: `framework/{agent,context,memory,retrieval,tools,evaluation,observability}/`, `applications/data-analyst-agent/`, plus `benchmarks/`, `datasets/`, `docs/`, `scripts/`, `tests/`.
- `docs_for_nano/` — Phase plans and architecture notes.
- `nanobot/tests/` and `my-agent-framework/tests/` — Tests mirror the source package path they cover.

## Build, Test, and Development Commands

```bash
pytest -q                                   # full suite
pytest tests/test_openai_api.py -v          # single module
ruff check nanobot/                         # lint
uv sync --all-extras --dev                  # CI-parity env
uv run --no-sync basedpyright               # strict types
cd webui && bun run build                   # WebUI -> nanobot/web/dist
nanobot gateway                             # run the gateway locally
```

Frontend checks: `cd webui && bun run lint && bun run test && bun run build`.

## Coding Style & Naming Conventions

- Python 3.11+, `asyncio` throughout; 100-character lines; `ruff` rules `E, F, I, N, W` (`E501` ignored).
- Use `snake_case` for modules/functions, `PascalCase` for classes, `UPPER_SNAKE_CASE` for constants.
- React/TypeScript components use `PascalCase.tsx`; hooks use `useThing.ts`.
- Prefer small, decoupled changes; do not mix formatting or import-sort churn into functional patches.

## Testing Guidelines

`pytest` with `asyncio_mode = "auto"`; coverage target is 75% (`fail_under = 75`). Name tests `test_<behavior>.py` and place them beside the mirrored package path. New channels or tools are self-contained packages auto-discovered by `pkgutil` scanning, so keep their tests inside the package. WebUI tests use Vitest (`bun run test`).

## Commit & Pull Request Guidelines

Commit subjects follow Conventional Commits with a scope, e.g. `fix(feishu): preserve QR query values`, `test(websocket): isolate delivery checks`, `docs: refresh README screenshots`. Reference issues as `(#1234)` or `(NAN-110)` when applicable.

PRs should: stay narrowly scoped, describe the change and linked issue, include screenshots for WebUI work, and pass `ruff`, `basedpyright`, `pytest`, and the WebUI lint/test/build.
