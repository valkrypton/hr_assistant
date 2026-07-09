# HR Assistant — Agent Guidance

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Setup

```bash
uv sync                     # creates .venv, installs pinned deps + dev tools from uv.lock
uv run pre-commit install --install-hooks --hook-type pre-commit --hook-type pre-push
cp .env.example .env        # set DATABASE_URL, AI_PROVIDER, and relevant keys
uv run alembic upgrade head # create/update hr_admin_users, hr_assistant_users, hr_assistant_audit
```

Schema changes to `core/rbac/models.py` go through Alembic from here on
(`uv run alembic revision --autogenerate --rev-id "NNNN_slug" -m "..."`, then
`uv run alembic upgrade head`) — the `migrations/` baseline reflects the schema
as of this commit. New migration files must follow the `NNNN_slug.py` naming
convention (`scripts/check_migration_naming.sh`, enforced by pre-commit).

Add/remove/upgrade a dependency: `uv add <pkg>` / `uv remove <pkg>` / `uv lock --upgrade-package <pkg>`.

## Running

```bash
uv run uvicorn api.main:app --reload
open index.html             # file:// — no server needed
```

The API is at `http://localhost:8000`. Interactive docs at `/docs`.
`GET /health` verifies both DB connections — check it first if the agent isn't responding.

## Architecture

The project is split into two packages that must never have circular imports;
`adapters/` (messaging) may import `core/` but never `api/`:

```
core/   — domain logic, zero dependency on api/
  execution.py — the query pipeline both api/ and adapters/ call
  db.py        — shared engines (app_engine, erp_engine) + db_session
  errors.py    — typed domain errors (UserNotRegistered, MissingRole, RateLimitExceeded, …)
  identity/    — AgentContext (wraps RBACContext) + resolver (Slack id → context)
  policies/    — permission layer: can(subject, action) / scope_for over ROLE_PERMISSIONS
  tools/       — Tool spec + registry; guarded query_erp_sql + typed tools (team/leave/joiners)
  runtimes/    — AgentRuntime interface; legacy (create_sql_agent) + langgraph implementations
  telemetry/   — write_audit + observability_fields
  rbac/        — RBACContext, sql_guard (scope enforcement), roles, models
  agent.py     — the legacy create_sql_agent path (wrapped by runtimes/legacy.py)
api/    — FastAPI HTTP layer, imports from core only
  routes/    — thin HTTP handlers: parse request → call a services/ function → map to a schemas/ response
  services/  — thin: call core.execution, map core errors to HTTP status codes
  schemas/   — Pydantic request/response models — one module per route file
  deps.py    — auth dependencies (require_admin, require_admin_unless_open), DbDep,
               and re-exports of the shared core.db engines/session.
adapters/ — messaging adapters (Slack); imports core/, never api/
```

**Import boundaries** (enforced by `tests/test_architecture.py` once the legacy
runtime is removed): `langchain`/`langgraph` imports live only in
`core/runtimes/`, `core/providers/`, and `core/vector_index.py`.

**Request flow:**
`POST /query` (`api/routes/query.py` → `api/services/query_service.py`) →
`core.execution.run_query()` (rate-limit → identity → runtime → redact → audit) →
`get_runtime().run()` → SQL guard → PostgreSQL. The Slack adapter
(`adapters/slack.py`) composes the same core steps around its own Slack I/O.

**Key routes:**
| Route | File | Purpose |
|---|---|---|
| `POST /query` | `api/routes/query.py` | Natural-language HR query; optional RBAC via `slack_user_id` |
| `GET /health` | `api/routes/health.py` | Pings both DBs; returns 503 if either is unreachable |
| `POST /webhook/slack` | `api/routes/slack.py` | Slack Events API handler |
| `GET/POST /users` | `api/routes/users.py` | Register / list / deactivate HR agent users |
| `GET /audit` | `api/routes/audit.py` | Query audit log |
| `/admin` | `api/admin.py` | SQLAdmin panel |

**LLM provider selection** (`core/config.py` → `core/providers/factory.py`):
`AI_PROVIDER` env var selects the backend. Ollama is the default. OpenAI-compatible providers (xAI/Grok, QWEN, LibreChat) reuse `langchain-openai` with a custom `base_url` — no extra packages needed.

**Agent runtimes** (`core/runtimes/`):
The agent framework sits behind an `AgentRuntime` interface, selected by the
`AGENT_RUNTIME` env var:
- `legacy` (default) — `langchain_community.create_sql_agent` in `core/agent.py`;
  `db.run` is monkey-patched through `sql_guard.rewrite_sql`.
- `langgraph` — `langchain.agents.create_agent` in `core/runtimes/langgraph/`;
  the model calls context-bound tools from the registry (scope comes from the
  resolved identity, never LLM args). Gated on a golden-query comparison
  (`scripts/compare_runtimes.py`) before becoming the default.

Only tables listed in `INCLUDED_TABLES` (comma-separated env var) are visible to
the SQL path.

**Tools & permissions** (`core/tools/`, `core/policies/`):
Business capabilities are `Tool`s that declare `required_permissions` and enforce
them in the tool layer via `policy.can()` — never via the prompt. `query_erp_sql`
is the guarded free-form escape hatch; typed tools (`team_roster`,
`leave_lookup`, `joiners_summary`) encode non-discoverable business rules and run
their SQL through the same guard, so scope is enforced once.

**Database:**
Two separate PostgreSQL connections:
- `DATABASE_URL` — read-only ERP database (queried by the SQL agent)
- `APP_DATABASE_URL` — writable app database (users, audit logs); required, no fallback

**RBAC** (`core/rbac/`):
Four roles: `cto_ceo`, `hr_manager`, `dept_head`, `team_lead`. Each role scopes what the agent may reveal. Forbidden columns (salary, NIC, DOB, etc.) are injected into every prompt regardless of role. Scope is enforced at the DB layer by `core/rbac/sql_guard.py` and proven by `tests/test_scope_execution.py`. Future enforcement directions (Postgres RLS, typed tools) are in [docs/rbac-hardening-roadmap.md](docs/rbac-hardening-roadmap.md).

**Slack adapter** (`adapters/slack.py`):
Verifies `X-Slack-Signature`, acks within 3 s, runs the agent in a FastAPI `BackgroundTask`, and posts Block Kit replies in-thread.

## Adding a new AI provider

1. Add config fields to `core/config.py` (follow the existing pattern).
2. Add a branch in `core/providers/factory.py` returning a `BaseChatModel`.
3. Add the provider name and keys to `.env.example`.
