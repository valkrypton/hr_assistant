# CLAUDE.md

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
`GET /health` verifies the DB connection — check it first if the agent isn't responding.

## Architecture

The project is split into two packages that must never have circular imports:

```
core/   — AI agent logic, zero dependency on api/
api/    — FastAPI HTTP layer, imports from core only
```

**Request flow:**
`index.html` → `POST /query` (`api/main.py`) → `core.agent.query()` → LangChain SQL agent → PostgreSQL (or SQLite)

**LLM provider selection** (`core/config.py` → `core/providers/factory.py`):
`AI_PROVIDER` env var selects the backend. Ollama is the default. OpenAI-compatible providers (xAI/Grok, QWEN) reuse `langchain-openai` with a custom `base_url` — no extra packages needed for them.

**SQL agent** (`core/agent.py`):
Uses `langchain_community.agent_toolkits.create_sql_agent`. On each call to `query()` it instantiates a fresh agent (no shared state). Tables listed in `IGNORED_TABLES` (comma-separated env var) are excluded from the database context passed to the LLM.

**Database:**
PostgreSQL. Set `DATABASE_URL` in `.env`. Use `IGNORED_TABLES` to hide sensitive tables from the agent.

## Adding a new AI provider

1. Add config fields to `core/config.py` (follow the existing pattern).
2. Add a branch in `core/providers/factory.py` returning a `BaseChatModel`.
3. Add the provider name and keys to `.env.example`.
