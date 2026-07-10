# Contributing

## Setup

Dependencies and the virtualenv are managed by [uv](https://docs.astral.sh/uv/) — never pip/venv directly:

```bash
uv sync                     # creates .venv, installs pinned deps + dev tools from uv.lock
uv run pre-commit install --install-hooks --hook-type pre-commit --hook-type pre-push
cp .env.example .env        # then set DATABASE_URL, AI_PROVIDER, and relevant keys
uv run alembic upgrade head # create/update the app-DB tables
```

Add/remove/upgrade a dependency: `uv add <pkg>` / `uv remove <pkg>` / `uv lock --upgrade-package <pkg>`.

## Code style

- [ruff](https://docs.astral.sh/ruff/) handles both linting and formatting; **line length is 100** (`[tool.ruff]` in `pyproject.toml`).
- The pre-commit hook runs `ruff format` and `ruff check --fix` on staged files — don't bypass it with `--no-verify`.
- Run manually anytime: `uv run ruff check .` and `uv run ruff format .`

## Architecture rule: no circular imports

The project is split into two packages that must never import each other circularly:

- `core/` — AI agent logic. **Zero dependency on `api/`** (or the messaging adapters).
- `api/` — FastAPI HTTP layer. Imports from `core/` only.

Keep new code on the right side of this line: business/agent logic goes in `core/`, HTTP concerns in `api/` (`routes/` → `services/` → `schemas/` layering).

## Database migrations

Schema changes to `core/rbac/models.py` go through Alembic:

```bash
uv run alembic revision --autogenerate --rev-id "NNNN_slug" -m "..."
uv run alembic upgrade head
```

Migration filenames **must** follow the `NNNN_slug.py` convention (e.g. `0003_add_team_index.py`) — enforced by the `scripts/check_migration_naming.sh` pre-commit hook.

## Tests

```bash
uv run pytest              # full suite; coverage + HTML/JUnit reports land in reports/
uv run pytest --no-cov -q  # quick run
```

The **pre-push git hook runs the full test suite** automatically, so a failing test blocks `git push`. New behavior should come with tests; RBAC/security changes must extend the scope-enforcement tests (`tests/test_scope_execution.py`, `tests/test_rbac.py`).

## Pull requests

- CI (GitHub Actions, `.github/workflows/ci.yml`) runs ruff + the full test suite on every PR; both must be green.
- Keep PRs focused — one logical change per PR.
- All pre-commit hooks (ruff, migration naming) and the full test suite must pass.
- Explain *why* in the description, not just what; link the relevant SPEC.md requirement when applicable.
- Do not re-introduce removed features (audit logging, rate limiting, vector search) — see the ❌ REMOVED banners in SPEC.md (FR-4, FR-6); they require an explicit product decision.
