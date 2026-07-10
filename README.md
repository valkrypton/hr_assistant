# HR Intelligence Agent

Natural-language workforce assistant that answers HR queries in plain English, backed by your company ERP (PostgreSQL). Ask questions like "Who's been on bench for 2 months?" or "Show resignations by department" and get accurate, data-driven answers.

## Features

- **Natural language to SQL** — LangChain SQL agent translates free-text questions into safe SELECT queries
- **Full schema context** — complete schema reference injected into every prompt; no chunking or vector search needed
- **Multi-provider LLM** — swap between Ollama (local), OpenAI, Anthropic, xAI/Grok, QWEN, or LibreChat (self-hosted gateway) via one env var — 6 providers
- **Read-only by design** — non-SELECT statements (INSERT / UPDATE / DELETE / DROP / …) are rejected by a sqlglot-based SQL guard (`core/rbac/sql_guard.py`) before execution; the agent prompt also instructs read-only behavior as advisory defense-in-depth
- **Privacy enforcement** — salary/compensation, national ID (NIC/CNIC), bank details, home/personal address, personal phone, personal email, date of birth, passport number, and medical records are never surfaced in responses (see `FORBIDDEN_COLUMNS` in `core/rbac/context.py`)
- **Table whitelist** — only tables listed in `INCLUDED_TABLES` are visible to the agent

## Architecture

```
core/   — AI agent logic (zero dependency on api/)
  agent.py                — LangChain SQL agent; injects full schema.md on every query
  config.py               — Settings (pydantic-settings), loaded from .env
  rbac/models.py          — SQLAlchemy models (source of truth for the DB schema)
  providers/factory.py    — LLM factory (Ollama / OpenAI / Anthropic / xAI / QWEN / LibreChat)
  context/
    schema.md             — Authoritative schema reference (tables, columns, business rules)

api/    — FastAPI HTTP layer (imports from core only)
  main.py                 — App setup, middleware, admin panel, router registration
  deps.py                 — Auth dependencies + DbDep (typed DB-session dependency)
  routes/                 — thin HTTP handlers — query, health, users, slack
  services/               — business logic per route (query_service, user_service)
  schemas/                — Pydantic request/response models per route
  admin.py                — SQLAdmin views

adapters/
  slack.py                — Slack Events API handler (signature verification, Block Kit replies)

migrations/                — Alembic migrations (schema source of truth going forward)
alembic.ini

scripts/
  seed_erp.py             — Create + seed a local ERP database with synthetic data
  create_admin.py         — Create / list / deactivate admin users (Basic Auth + /admin login)
  check_migration_naming.sh — Pre-commit hook: enforce NNNN_slug.py migration names

Dockerfile                 — Container image (uv sync --frozen --no-dev, uvicorn on $PORT)
railway.toml               — Railway deploy config (healthcheck on /health)

index.html                — Single-file web UI (no server needed, works from file://)
```

**Request flow:**
`index.html` / Slack → `POST /query` / `POST /webhook/slack` → `core.agent.query()` → LangChain SQL agent → PostgreSQL

## Setup

```bash
uv sync                     # creates .venv, installs pinned deps + dev tools from uv.lock
uv run pre-commit install --install-hooks --hook-type pre-commit --hook-type pre-push
cp .env.example .env
```

Edit `.env`:

| Variable | Description |
|---|---|
| `DATABASE_URL` | PostgreSQL connection string (ERP, read-only) |
| `APP_DATABASE_URL` | PostgreSQL connection string (app DB — users) |
| `AI_PROVIDER` | `ollama` (default) \| `openai` \| `anthropic` \| `xai` \| `qwen` \| `librechat` |
| `INCLUDED_TABLES` | Comma-separated whitelist of tables the agent may query |
| `SLACK_BOT_TOKEN` | Slack bot OAuth token (`xoxb-…`) |
| `SLACK_SIGNING_SECRET` | Slack signing secret for request verification |
| `SECRET_KEY` | Signs admin session cookies (`/admin` panel). Required in production (startup error when `DEBUG=false` and unset) — generate with `openssl rand -hex 32` |
| `DEBUG` | Default `false`. `true` enables verbose agent logging, console-format logs, and the dev-only allowances below |
| `ALLOW_UNAUTHENTICATED_QUERY` | Dev-only. `true` lets `POST /query` run without admin auth. Startup `RuntimeError` if `true` while `DEBUG=false` (`core/config.py:146-153`) |
| `CORS_ALLOW_ORIGINS` | Comma-separated browser origins. Default `*` — wildcard is forbidden in production (startup error when `DEBUG=false`, `core/config.py:174-181`) |
| `TRUSTED_PROXY_HOSTS` | Proxy/load-balancer hosts trusted for `X-Forwarded-*` headers (default `127.0.0.1`) |

Create the app-DB tables (`hr_admin_users`, `hr_assistant_users`):

```bash
uv run alembic upgrade head
```

Schema changes to `core/rbac/models.py` go through Alembic from here on:
`uv run alembic revision --autogenerate --rev-id "NNNN_slug" -m "..."` to generate a migration
(filename must follow the `NNNN_slug.py` convention — enforced by a pre-commit hook), then
`uv run alembic upgrade head` to apply it.

### Local ERP (no real ERP database)

If you don't have a company ERP, use the seed script to create a local PostgreSQL database with realistic synthetic data (~500 employees).

**1. Create a local PostgreSQL database:**

```bash
createdb hr_erp_local
```

**2. Point `DATABASE_URL` at it in `.env`:**

```
DATABASE_URL=postgresql://localhost/hr_erp_local
```

**3. Create tables and seed data:**

```bash
uv run python scripts/seed_erp.py
```

This creates all ERP tables and populates them with 500 employees (420 active, 80 exited), 22 teams, leave records, weekly time logs, competency ratings, skill assignments, and job requisitions.

| Flag | Effect |
|---|---|
| _(none)_ | Create tables + seed data |
| `--reset` | Drop all ERP tables first, then recreate and seed |
| `--tables-only` | DDL only — no data inserted |

**4. Expose the tables to the agent** — add to `.env`:

```
INCLUDED_TABLES=department,employment_type,competency_role,competency_level,designation,leave_type,skill_category,person,team,person_team,leave_limit,person_leave_limit,leave_record,holiday_record,person_week_log,person_week_project,person_competency,users_personresignation,core_personstatushistory,core_personemploymenthistory,core_personemploymenttypehistory,person_skill_category,job_requisition,annual_review_response
```

## Running

```bash
uv run uvicorn api.main:app --reload
open index.html    # or just open in your browser — no server needed
```

- API: `http://localhost:8000`
- Docs: `http://localhost:8000/docs`
- Admin: `http://localhost:8000/admin`
- Health check: `GET /health` — checks BOTH databases; returns `{"status":"ok","erp_database":"connected","app_database":"connected"}`, or 503 if either database is unreachable

## API

### `POST /query`

Natural-language HR query. **Requires admin HTTP Basic Auth by default** (`require_admin_unless_open`, `api/deps.py:57-69`); `ALLOW_UNAUTHENTICATED_QUERY=true` is a dev-only override — the server refuses to start with it in production (`DEBUG=false` → startup `RuntimeError`). When auth is on, the bundled `index.html` prompts for credentials on the first 401 and keeps them in memory only.

`slack_user_id` in the body selects the RBAC scope of a *registered* user — it is **NOT authentication** (Slack IDs are public within a workspace). **If you omit `slack_user_id`, the query runs unrestricted** (no RBAC scoping) — be deliberate about that. End-user traffic should go through the signature-verified Slack webhook instead.

```bash
curl -X POST http://localhost:8000/query \
  -u admin:yourpassword \
  -H "Content-Type: application/json" \
  -d '{"query": "How many employees do we have?", "slack_user_id": "U012AB3CD"}'
```

### `/users`

All `/users` routes require admin HTTP Basic Auth. Create an admin first:

```bash
uv run python scripts/create_admin.py <username>
```

| Route | Behavior |
|---|---|
| `GET /users` | Returns **active users only** |
| `POST /users` | Registers a user — `201` on success, `409` if the `slack_user_id` is already registered |
| `DELETE /users/{id}` | **Soft-deletes** (sets `is_active=false`) — `204` on success, `404` if not found |

```bash
curl -u admin:yourpassword -X POST http://localhost:8000/users \
  -H "Content-Type: application/json" \
  -d '{"employee_id": 1, "role": "hr_manager", "slack_user_id": "U012AB3CD"}'
```

### `GET /health`

Public (no auth). Pings both the ERP and app databases:

```json
{"status": "ok", "erp_database": "connected", "app_database": "connected"}
```

Returns `503` if either database is unreachable.

### Administration

Admin users power **two separate auth mechanisms**, both backed by the same `hr_admin_users` table:

1. **HTTP Basic Auth** on the `/users` and `/query` API routes (credentials checked per request).
2. **Session-cookie login form** on the `/admin` panel (SQLAdmin; cookie signed with `SECRET_KEY`).

Manage admins with the CLI:

```bash
uv run python scripts/create_admin.py <username>       # create (prompts for password)
uv run python scripts/create_admin.py --list           # list all admins
uv run python scripts/create_admin.py --deactivate <username>  # revoke access
```

## Slack Setup

The bot handles `@hr-agent` mentions and direct messages via the Slack Events API.

### 1. Create a Slack app

1. Go to [api.slack.com/apps](https://api.slack.com/apps) → **Create New App** → **From scratch**.
2. Name it (e.g. `HR Assistant`) and pick your workspace.

### 2. Set OAuth scopes

Under **OAuth & Permissions → Scopes → Bot Token Scopes**, add:

| Scope | Purpose |
|---|---|
| `app_mentions:read` | Receive `@hr-agent` mentions |
| `chat:write` | Post replies |
| `im:history` | Read DM threads for conversation continuity |
| `im:read` | Receive DMs |
| `im:write` | Open DM channels |
| `channels:history` | Read thread history in public channels |

Click **Install to Workspace** and copy the **Bot User OAuth Token** (`xoxb-…`).

### 3. Get the signing secret

Under **Basic Information → App Credentials**, copy the **Signing Secret**.

### 4. Add credentials to `.env`

```
SLACK_BOT_TOKEN=xoxb-your-bot-token
SLACK_SIGNING_SECRET=your-signing-secret
```

### 5. Expose the local server

Slack must reach your server over HTTPS. Use [ngrok](https://ngrok.com) for local dev:

```bash
ngrok http 8000
```

Copy the `https://…ngrok-free.app` URL.

### 6. Enable Event Subscriptions

Under **Event Subscriptions**:

- Toggle **Enable Events** on.
- Set **Request URL** to `https://<your-ngrok-url>/webhook/slack`.
  Slack will send a verification challenge — the server handles it automatically.
- Under **Subscribe to bot events**, add:
  - `app_mention`
  - `message.im`

Save changes.

### 7. Register users

The bot enforces RBAC — every Slack user must be registered with a role before they can query. The `/users` API requires admin Basic Auth — create an admin first with `uv run python scripts/create_admin.py <username>`, then:

```bash
# Register a user (replace values as needed)
curl -u admin:yourpassword -X POST http://localhost:8000/users \
  -H "Content-Type: application/json" \
  -d '{
    "employee_id": 1,
    "role": "hr_manager",
    "slack_user_id": "U012AB3CD"
  }'
```

Available roles: `cto_ceo`, `hr_manager`, `dept_head`, `team_lead`.

To find a user's Slack ID: open their Slack profile → **⋮** → **Copy member ID**.

### 8. Test it

Mention the bot in any channel it has been invited to, or send it a DM:

```
@hr-agent Who's been on bench for 2 months?
```

Replies arrive as threaded Block Kit cards within 15 seconds.

## LLM Providers

| Provider | `AI_PROVIDER` | Required env vars |
|---|---|---|
| Ollama (local) | `ollama` | `OLLAMA_BASE_URL`, `OLLAMA_MODEL` |
| OpenAI | `openai` | `OPENAI_API_KEY`, `OPENAI_MODEL` |
| Anthropic | `anthropic` | `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL` |
| xAI / Grok | `xai` | `XAI_API_KEY`, `XAI_MODEL`, `XAI_BASE_URL` |
| QWEN | `qwen` | `QWEN_API_KEY`, `QWEN_MODEL`, `QWEN_BASE_URL` |
| LibreChat (self-hosted LiteLLM) | `librechat` | `LIBRECHAT_API_KEY`, `LIBRECHAT_MODEL`, `LIBRECHAT_BASE_URL` |

To add a new provider: add config fields to `core/config.py`, add a branch in `core/providers/factory.py` returning a `BaseChatModel`, and update `.env.example`.

## Example Queries

```
Who hasn't filled their daily logs this week?
Who's been non-billable for the last 2 months?
Show resignations by department
How many new joiners did we have in 2025?
Which team has the most attrition this year?
Who's available for a Django project starting May?
What is Bilal Qureshi's competency score?
```

## Testing

```bash
uv run pytest              # full suite with coverage; HTML + JUnit reports land in reports/
uv run pytest --no-cov -q  # quick run, no coverage
```

Coverage and test reports are written to `reports/` (`reports/coverage/html`, `reports/coverage/coverage.xml`, `reports/unittests/html`, `reports/unittests/junit.xml`) per `[tool.pytest.ini_options]` in `pyproject.toml`. The pre-push git hook runs the full suite automatically before `git push`.

Test files:

- `tests/test_rbac.py` — role scoping, forbidden columns, SQL guard
- `tests/test_scope_execution.py` — DB-layer scope enforcement proofs
- `tests/test_sql_guard_dangerous_functions.py` — dangerous SQL function blocking
- `tests/test_e2e.py` — end-to-end canonical query coverage
- `tests/test_agent_scoped_run.py` — agent RBAC integration
- `tests/test_config_guards.py` — production startup guards
- `tests/test_slack_adapter.py`, `tests/test_slack_dedupe.py` — Slack webhook handling

## Observability

Per-query latency and token counts are emitted to the server logs via structlog only — nothing is persisted (audit logging was deliberately removed; see SPEC.md FR-6). `DEBUG=true` renders human-readable console logs; otherwise logs are single-line JSON.

## Deployment

See [docs/deployment.md](docs/deployment.md) — Docker image, Railway config, and the production startup checklist (`SECRET_KEY`, `APP_DATABASE_URL`, CORS, auth guards).

## Development Phases

| Phase | Status | Goal |
|---|---|---|
| 0 — Foundation | Complete | Local prototype, SQLite seed, web UI |
| 1 — Production Data Layer | Complete | Real PostgreSQL ERP, full schema context |
| 2 — RBAC | Complete | Role-scoped answers per requester |
| 3 — Slack | Complete | `@hr-agent` mentions with Block Kit cards |
| 4 — Hardening | Complete | Retry logic, E2E tests, secrets rotation guide |
| 5 — Backend structure | Complete | Alembic migrations, `services/`/`schemas/` layering, typed DB-session dependency, pydantic-settings config |

> Note: per-user rate limiting and audit/token-usage tracking were built during Phase 4 and then **deliberately removed** (query recording is not wanted) — see [SPEC.md FR-6](SPEC.md) before considering re-adding either.
