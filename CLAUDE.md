# CLAUDE.md

See [agents.md](AGENTS.md) for full codebase guidance.

## Tooling

- Deps/env managed by `uv` (not pip/venv) — use `uv sync`, `uv run <cmd>`, `uv add`/`uv remove`.
- `pre-commit` runs ruff (lint + format) on commit; don't bypass with `--no-verify`.
