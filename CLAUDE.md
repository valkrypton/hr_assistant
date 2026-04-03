# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # fill in AI_PROVIDER and relevant keys
python3 data/seed.py        # create/reset data/company.db
```

## Running

```bash
# API server (reload on change)
uvicorn api.main:app --reload

# Open the UI
open index.html             # file:// — no server needed
```

The API is at `http://localhost:8000`. Interactive docs at `/docs`.

## Re-seeding the database

`data/seed.py` **drops and recreates** `data/company.db` from scratch each run. Run it any time you need a clean slate.

## Architecture

The project is split into two packages that must never have circular imports:

```
core/   — AI agent logic, zero dependency on api/
api/    — FastAPI HTTP layer, imports from core only
```

**Request flow:**
`index.html` → `POST /query` (`api/main.py`) → `core.agent.query()` → LangChain SQL agent → SQLite

**LLM provider selection** (`core/config.py` → `core/providers/factory.py`):
`AI_PROVIDER` env var selects the backend. Ollama is the default. OpenAI-compatible providers (xAI/Grok, QWEN) reuse `langchain-openai` with a custom `base_url` — no extra packages needed for them.

**SQL agent** (`core/agent.py`):
Uses `langchain_community.agent_toolkits.create_sql_agent`. On each call to `query()` it instantiates a fresh agent (no shared state). Tables listed in `IGNORED_TABLES` (comma-separated env var) are excluded from the database context passed to the LLM.

**Database** (`data/company.db`):
SQLite with 7 tables: `departments`, `employees`, `attendance`, `leaves`, `payroll`, `performance_reviews`, `job_postings`. Schema is defined inline in `data/seed.py`.

## Adding a new AI provider

1. Add config fields to `core/config.py` (follow the existing pattern).
2. Add a branch in `core/providers/factory.py` returning a `BaseChatModel`.
3. Add the provider name and keys to `.env.example`.
