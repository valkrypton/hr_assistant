---
name: hr-assistant
description: >
  Development context skill for the HR Intelligence Agent project at
  /Users/ali.tariq/hr_assistant. Load this skill whenever the user wants to make
  code changes, add features, fix bugs, extend the data model, add a new AI provider,
  or work on any part of the hr_assistant codebase. Trigger on any mention of the
  HR agent, HR assistant, the company database, the query endpoint, or changes to
  core/ or api/ in this project.
---

# HR Assistant — Developer Context

Project root: `/Users/ali.tariq/hr_assistant`

---

## Architecture in one sentence

`core/` is the brain; `api/` is the mouth. **They must never import from each other in reverse** — `api/` imports from `core/`, never the other way around.

```
index.html  →  POST /query  (api/main.py)
            →  core.agent.query()
            →  LangChain SQL agent  (create_sql_agent)
            →  PostgreSQL  (DATABASE_URL in .env)
```

---

## Key files

| File | What it owns |
|------|-------------|
| `core/config.py` | All env-var settings — provider, model names, DB URL, INCLUDED_TABLES |
| `core/providers/factory.py` | `get_llm()` — returns a `BaseChatModel` based on `AI_PROVIDER` |
| `core/agent.py` | `query(user_input) -> str` — singleton agent, built once on first query |
| `core/context/schema.md` | Static schema context loaded into the agent prefix at startup — table descriptions, column meanings, business rules, common join patterns |
| `api/main.py` | FastAPI app, CORS, `POST /query`, `GET /health` (real DB ping) |
| `SPEC.md` | Full functional requirements and development phase plan |
| `skills.md` | User-facing capability reference (what queries the agent can answer) |

---

## Expected database schema (PostgreSQL)

### `employees` — most queries touch this table
```
id, first_name, last_name, email, phone,
job_title,            -- free-form display title
competency_role,      -- canonical role bucket (Software Engineer | QA Engineer |
                      --   Product Manager | DevOps Engineer | UI/UX Designer |
                      --   HR Specialist | HR Manager | Finance Manager |
                      --   Financial Analyst | Sales Executive | Sales Manager |
                      --   Marketing Specialist | Content Writer | Graphic Designer |
                      --   Operations Specialist | Logistics Coordinator |
                      --   Recruitment Specialist | Payroll Specialist | Engineering Manager)
department_id,        -- FK → departments
manager_id,           -- FK → employees (self-ref, nullable)
hire_date,            -- ISO-8601 text
salary,               -- REAL (NEVER expose in agent responses)
employment_type,      -- full_time | part_time | contractor
status,               -- active | on_leave | terminated
competency_score,     -- REAL 0-100
exit_date,            -- ISO-8601 text, NULL if still employed
exit_type             -- resignation | termination | NULL
```

**Employment classification used in queries:**
- "employed" = `full_time` OR `part_time`
- "subcontractor" = `contractor`

### Other tables
```
departments        — id, name, location, budget
attendance         — employee_id, work_date, check_in, check_out, status
leaves             — employee_id, leave_type, start_date, end_date, status, approved_by, reason
payroll            — employee_id, pay_year, pay_month, base_salary, bonus, deductions, net_salary, payment_date
performance_reviews — employee_id, reviewer_id, review_date, rating (1-5), comments
job_postings       — title, department_id, posted_date, closing_date, min_salary, max_salary, status
```

---

## Running the project

```bash
# First time
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # set DATABASE_URL, AI_PROVIDER + keys

# Start API
uvicorn api.main:app --reload

# Verify DB connection
curl http://localhost:8000/health

# Open UI (no server needed)
open index.html
```

---

## Common change patterns

### Add a new AI provider
1. Add env-var fields to `core/config.py` following the existing pattern
2. Add a branch in `core/providers/factory.py` returning a `BaseChatModel`
3. Add the provider name and keys to `.env.example`
4. No other files need to change

### Add a new API route
1. Add the route to `api/main.py` only — never touch `core/` for API concerns
2. If the route needs agent logic, call functions from `core/agent.py`

### Change the agent's behaviour / prompt
- Edit the `_PREFIX` string in `core/agent.py` for rules and persona
- Edit `core/context/schema.md` for schema descriptions, business rules, and query hints
- The agent is a singleton — restart the server after any change to pick it up

### Update the schema context
- `core/context/schema.md` is the source of truth for what the agent knows about the DB
- When production table columns change, update this file — the agent won't auto-detect drift
- Add new join patterns and business rules here; the more precise the hints, the better local models perform

---

## Rules to never break

- `core/` has **zero imports** from `api/` — circular imports will crash the server on startup
- The SQL agent must only issue SELECT statements — the prompt enforces this; don't weaken it
- `salary`, bank details, personal phone numbers, and home addresses must **never** appear in agent responses

---

## Gotchas

- `create_sql_agent` (not `AgentExecutor` + `create_react_agent`) — the latter was removed from `langchain.agents` in v0.3+
- The UI is opened as `file://` so the API has `allow_origins=["*"]` CORS — acceptable for local dev, tighten before production
- The default Ollama model in `config.py` is `llama3.1` — change `OLLAMA_MODEL` in `.env` if you're running a different model locally
- Set `INCLUDED_TABLES` in `.env` to the exact tables the agent needs — the agent will refuse to start if this is empty
