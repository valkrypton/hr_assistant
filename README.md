# HR Intelligence Agent

Natural-language workforce assistant that answers HR queries in plain English, backed by your company ERP (PostgreSQL). Ask questions like "Who's been on bench for 2 months?" or "Show resignations by department" and get accurate, data-driven answers.

## Features

- **Natural language to SQL** — LangChain SQL agent translates free-text questions into safe SELECT queries
- **Schema-aware RAG** — relevant schema context is retrieved per query via semantic search (Chroma), not embedded in every prompt
- **Multi-provider LLM** — swap between Ollama (local), OpenAI, Anthropic, xAI/Grok, or QWEN via one env var
- **Read-only by design** — INSERT / UPDATE / DELETE / DROP are blocked at the prompt level
- **Privacy enforcement** — salary, NIC, phone, email, and date of birth are never surfaced in responses
- **Table whitelist** — only tables listed in `INCLUDED_TABLES` are visible to the agent

## Architecture

```
core/   — AI agent logic (zero dependency on api/)
  agent.py              — LangChain SQL agent, query-time schema RAG
  config.py             — Settings loaded from .env
  providers/factory.py  — LLM factory (Ollama / OpenAI / Anthropic / xAI / QWEN)
  vector_index.py       — Chroma index over team/project descriptions (semantic search)
  context/
    schema.md           — Authoritative schema reference (tables, columns, business rules)
    schema_index.py     — Chunks schema.md and builds "hr_schema" Chroma collection

api/    — FastAPI HTTP layer (imports from core only)
  main.py               — POST /query, GET /health, CORS

scripts/
  reindex.py            — Rebuild Chroma collections (--erp and/or --schema flags)

index.html              — Single-file web UI (no server needed, works from file://)
```

**Request flow:**
`index.html` → `POST /query` → `core.agent.query()` → schema RAG + LangChain SQL agent → PostgreSQL

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env`:

| Variable | Description |
|---|---|
| `DATABASE_URL` | PostgreSQL connection string |
| `AI_PROVIDER` | `ollama` (default) \| `openai` \| `anthropic` \| `xai` \| `qwen` |
| `INCLUDED_TABLES` | Comma-separated whitelist of tables the agent may query |
| `VECTOR_STORE_PATH` | Where to persist Chroma DB (default: `./data/chroma`) |
| `VECTOR_EMBEDDING_MODEL` | Ollama embedding model (default: `nomic-embed-text`) |

### Embedding model (Ollama only)

```bash
ollama pull nomic-embed-text   # 274 MB, recommended
# or use any model already installed, e.g. llama3.2:3b
```

### Build vector indices

```bash
python scripts/reindex.py --schema   # schema section index (run once, or after schema.md changes)
python scripts/reindex.py --erp      # team/project descriptions index (run nightly)
python scripts/reindex.py            # rebuild both
```

## Running

```bash
uvicorn api.main:app --reload
open index.html    # or just open in your browser — no server needed
```

- API: `http://localhost:8000`
- Docs: `http://localhost:8000/docs`
- Health check: `GET /health` — returns 503 if DB is unreachable

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
| 1 — Production Data Layer | In progress | Real PostgreSQL ERP, schema RAG, vector index |
| 2 — RBAC | Planned | Role-scoped answers per requester |
| 3 — WhatsApp | Planned | Natural language via WhatsApp Business API |
| 4 — Slack | Planned | `@hr-agent` mentions with Block Kit cards |
| 5 — Hardening | Planned | Rate limits, cost tracking, load testing |
