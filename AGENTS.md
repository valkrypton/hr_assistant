# HR Assistant — Agent Guidance

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # set DATABASE_URL, AI_PROVIDER, and relevant keys
```

## Running

```bash
uvicorn api.main:app --reload
open index.html             # file:// — no server needed
```

The API is at `http://localhost:8000`. Interactive docs at `/docs`.
`GET /health` verifies both DB connections — check it first if the agent isn't responding.

## Architecture

The project is split into two packages that must never have circular imports:

```
core/   — AI agent logic, zero dependency on api/
api/    — FastAPI HTTP layer, imports from core only
```

**Request flow:**
`index.html` → `POST /query` (`api/routes/query.py`) → `core.agent.query()` → LangChain SQL agent → PostgreSQL

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

**SQL agent** (`core/agent.py`):
Uses `langchain_community.agent_toolkits.create_sql_agent`. On each call to `query()` it instantiates a fresh agent (no shared state). Only tables listed in `INCLUDED_TABLES` (comma-separated env var) are visible to the agent — all others are hidden.

**Database:**
Two separate PostgreSQL connections:
- `DATABASE_URL` — read-only ERP database (queried by the SQL agent)
- `APP_DATABASE_URL` — writable app database (users, audit logs); defaults to `DATABASE_URL` for local dev

**RBAC** (`core/rbac/`):
Four roles: `cto_ceo`, `hr_manager`, `dept_head`, `team_lead`. Each role scopes what the agent may reveal. Forbidden columns (salary, NIC, DOB, etc.) are injected into every prompt regardless of role.

**Slack adapter** (`adapters/slack.py`):
Verifies `X-Slack-Signature`, acks within 3 s, runs the agent in a FastAPI `BackgroundTask`, and posts Block Kit replies in-thread.

## Adding a new AI provider

1. Add config fields to `core/config.py` (follow the existing pattern).
2. Add a branch in `core/providers/factory.py` returning a `BaseChatModel`.
3. Add the provider name and keys to `.env.example`.
