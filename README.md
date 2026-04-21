# HR Intelligence Agent

Natural-language workforce assistant that answers HR queries in plain English, backed by your company ERP (PostgreSQL). Ask questions like "Who's been on bench for 2 months?" or "Show resignations by department" and get accurate, data-driven answers.

## Features

- **Natural language to SQL** — LangChain SQL agent translates free-text questions into safe SELECT queries
- **Full schema context** — complete schema reference injected into every prompt; no chunking or vector search needed
- **Multi-provider LLM** — swap between Ollama (local), OpenAI, Anthropic, xAI/Grok, or QWEN via one env var
- **Read-only by design** — INSERT / UPDATE / DELETE / DROP are blocked at the prompt level
- **Privacy enforcement** — salary, NIC, phone, email, and date of birth are never surfaced in responses
- **Table whitelist** — only tables listed in `INCLUDED_TABLES` are visible to the agent

## Architecture

```
core/   — AI agent logic (zero dependency on api/)
  agent.py              — LangChain SQL agent; injects full schema.md on every query
  config.py             — Settings loaded from .env
  providers/factory.py  — LLM factory (Ollama / OpenAI / Anthropic / xAI / QWEN)
  vector_index.py       — Chroma index over team/project descriptions (FR-4 semantic search)
  context/
    schema.md           — Authoritative schema reference (tables, columns, business rules)

api/    — FastAPI HTTP layer (imports from core only)
  main.py               — App setup, middleware, admin panel, router registration
  routes/               — query, health, audit, users, slack endpoints
  admin.py              — SQLAdmin views

adapters/
  slack.py              — Slack Events API handler (signature verification, Block Kit replies)

scripts/
  reindex.py            — Rebuild ERP content Chroma index (run nightly)

index.html              — Single-file web UI (no server needed, works from file://)
```

**Request flow:**
`index.html` / Slack → `POST /query` / `POST /webhook/slack` → `core.agent.query()` → LangChain SQL agent → PostgreSQL

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env`:

| Variable | Description |
|---|---|
| `DATABASE_URL` | PostgreSQL connection string (ERP, read-only) |
| `APP_DATABASE_URL` | PostgreSQL connection string (app DB — users, audit logs) |
| `AI_PROVIDER` | `ollama` (default) \| `openai` \| `anthropic` \| `xai` \| `qwen` \| `librechat` |
| `INCLUDED_TABLES` | Comma-separated whitelist of tables the agent may query |
| `SLACK_BOT_TOKEN` | Slack bot OAuth token (`xoxb-…`) |
| `SLACK_SIGNING_SECRET` | Slack signing secret for request verification |
| `RATE_LIMIT_PER_HOUR` | Max queries per user per hour (default: 30; set 0 to disable) |
| `VECTOR_STORE_PATH` | Where to persist Chroma DB for ERP semantic search (default: `./data/chroma`) |
| `VECTOR_EMBEDDING_MODEL` | Ollama embedding model for ERP search (default: `nomic-embed-text`) |

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
python scripts/seed_erp.py
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

### ERP semantic search index (FR-4 only)

Only needed if you want "who has Sabre API experience?"-style queries over free-text project/log data:

```bash
ollama pull nomic-embed-text          # 274 MB embedding model
python scripts/reindex.py             # index team/project descriptions
```

Schedule `scripts/reindex.py` nightly to keep the index fresh.

## Running

```bash
uvicorn api.main:app --reload
open index.html    # or just open in your browser — no server needed
```

- API: `http://localhost:8000`
- Docs: `http://localhost:8000/docs`
- Admin: `http://localhost:8000/admin`
- Health check: `GET /health` — returns 503 if DB is unreachable

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

The bot enforces RBAC — every Slack user must be registered with a role before they can query. Use the API:

```bash
# Register a user (replace values as needed)
curl -X POST http://localhost:8000/users \
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
Who has experience with Sabre APIs?
What is Bilal Qureshi's competency score?
```

## Development Phases

| Phase | Status | Goal |
|---|---|---|
| 0 — Foundation | Complete | Local prototype, SQLite seed, web UI |
| 1 — Production Data Layer | Complete | Real PostgreSQL ERP, full schema context |
| 2 — RBAC | Complete | Role-scoped answers per requester |
| 3 — Slack | Complete | `@hr-agent` mentions with Block Kit cards |
| 4 — Hardening | Complete | Rate limits, token tracking, retry logic, E2E tests |
