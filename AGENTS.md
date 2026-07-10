# HR Assistant — Agent Guidance

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Setup

```bash
uv sync                     # creates .venv, installs pinned deps + dev tools from uv.lock
uv run pre-commit install --install-hooks --hook-type pre-commit --hook-type pre-push
cp .env.example .env        # set DATABASE_URL, AI_PROVIDER, and relevant keys
uv run alembic upgrade head # create/update hr_admin_users, hr_assistant_users
```

Schema changes to `core/rbac/models.py` go through Alembic from here on
(`uv run alembic revision --autogenerate --rev-id "NNNN_slug" -m "..."`, then
`uv run alembic upgrade head`) — the `migrations/` baseline reflects the schema
as of this commit. New migration files must follow the `NNNN_slug.py` naming
convention (`scripts/check_migration_naming.sh`, enforced by pre-commit).

Add/remove/upgrade a dependency: `uv add <pkg>` / `uv remove <pkg>` / `uv lock --upgrade-package <pkg>`.

Before using `/users`, `/query`, or the `/admin` panel, create an admin user
(HTTP Basic Auth + `/admin` session login are both backed by `hr_admin_users`):

```bash
uv run python scripts/create_admin.py <username>   # also: --list, --deactivate <username>
```

## Running

```bash
uv run uvicorn api.main:app --reload
open index.html             # file:// — no server needed
```

The API is at `http://localhost:8000`. Interactive docs at `/docs`.
`GET /health` verifies both DB connections — check it first if the agent isn't responding.

## Testing

```bash
uv run pytest              # full suite; coverage + HTML/JUnit reports land in reports/
uv run pytest --no-cov -q  # quick run
```

The pre-push git hook runs the full suite automatically (`.pre-commit-config.yaml`),
so a failing test blocks `git push`.

## Architecture

The project is split into two packages that must never have circular imports:

```
core/   — AI agent logic, zero dependency on api/
api/    — FastAPI HTTP layer, imports from core only
  routes/    — thin HTTP handlers: parse request → call a services/ function → map to a schemas/ response
  services/  — business logic (RBAC resolution, DB queries) — one module per route file
  schemas/   — Pydantic request/response models — one module per route file
  deps.py    — auth dependencies (require_admin, require_admin_unless_open) and DbDep,
               the typed DB-session dependency (one Session per request, injected via
               Depends). Routes with a slow call in the middle (e.g. /query's LLM agent
               call, which can take ~15s) use db_session() directly in short scopes
               instead of DbDep, so a pool connection isn't held open across it.
```

**Request flow:**
`index.html` → `POST /query` (`api/routes/query.py` → `api/services/query_service.py`) → `core.agent.query()` → LangChain SQL agent → PostgreSQL

**Key routes:**
| Route | File | Purpose | Auth |
|---|---|---|---|
| `POST /query` | `api/routes/query.py` | Natural-language HR query; optional RBAC via `slack_user_id` | Basic Auth unless `ALLOW_UNAUTHENTICATED_QUERY` (dev only) |
| `GET /health` | `api/routes/health.py` | Pings both DBs; returns 503 if either is unreachable | Public |
| `POST /webhook/slack` | `api/routes/slack.py` | Slack Events API handler | Slack signature verification |
| `GET/POST /users` | `api/routes/users.py` | Register / list HR agent users | Basic Auth (`require_admin`) |
| `DELETE /users/{user_id}` | `api/routes/users.py` | Deactivate a user (soft delete, `is_active=false`) | Basic Auth (`require_admin`) |
| `/admin` | `api/admin.py` | SQLAdmin panel | Session-cookie login |

**LLM provider selection** (`core/config.py` → `core/providers/factory.py`):
`AI_PROVIDER` env var selects the backend. Ollama is the default. OpenAI-compatible providers (xAI/Grok, QWEN, LibreChat) reuse `langchain-openai` with a custom `base_url` — no extra packages needed.

**SQL agent** (`core/agent.py`):
Uses `langchain_community.agent_toolkits.create_sql_agent`. On each call to `query()` it instantiates a fresh agent (no shared state). Only tables listed in `INCLUDED_TABLES` (comma-separated env var) are visible to the agent — all others are hidden.

**Database:**
Two separate PostgreSQL connections:
- `DATABASE_URL` — read-only ERP database (queried by the SQL agent)
- `APP_DATABASE_URL` — writable app database (users); required, no fallback

**RBAC** (`core/rbac/`):
Four roles: `cto_ceo`, `hr_manager`, `dept_head`, `team_lead`. Each role scopes what the agent may reveal. Forbidden columns (salary, NIC, DOB, etc.) are injected into every prompt regardless of role. Scope is enforced at the DB layer by `core/rbac/sql_guard.py` and proven by `tests/test_scope_execution.py`. Future enforcement directions (Postgres RLS, typed tools) are in [docs/rbac-hardening-roadmap.md](docs/rbac-hardening-roadmap.md).

**Slack adapter** (`adapters/slack.py`):
Verifies `X-Slack-Signature`, acks within 3 s, runs the agent in a FastAPI `BackgroundTask`, and posts Block Kit replies in-thread.

## Adding a new AI provider

1. Add config fields to `core/config.py` (follow the existing pattern).
2. Add a branch in `core/providers/factory.py` returning a `BaseChatModel`.
3. Add the provider name and keys to `.env.example`.
