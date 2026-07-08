# CLAUDE.md

See [agents.md](AGENTS.md) for full codebase guidance.

## Tooling

- Deps/env managed by `uv` (not pip/venv) — use `uv sync`, `uv run <cmd>`, `uv add`/`uv remove`.
- `pre-commit` runs ruff (lint + format) on commit; don't bypass with `--no-verify`.

## Model usage policy

- Use Fable 5 (Opus-class) only for security work and critical/high-severity issues (design, review, and fixes).
- Never run or iterate on tests in a Fable session — delegate test runs and test-writing to a cheaper model (e.g. the Agent tool with `model: haiku` or `model: sonnet`).
- Routine implementation, refactors, and docs should use Sonnet or Haiku.
