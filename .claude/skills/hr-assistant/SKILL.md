---
name: hr-assistant
description: >
  Development context skill for the HR Intelligence Agent project at
  /Users/ali.tariq/hr_assistant. Load this skill whenever the user wants to make
  code changes, add features, fix bugs, extend the data model, add a new AI provider,
  or work on any part of the hr_assistant codebase. Trigger on any mention of the
  HR agent, HR assistant, the company database, the query endpoint, or changes to
  core/, api/, or data/ in this project.
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
            →  SQLite  (data/company.db)
```

---

## Key files

| File | What it owns |
|------|-------------|
| `core/config.py` | All env-var settings — provider, model names, DB URL, IGNORED_TABLES |
| `core/providers/factory.py` | `get_llm()` — returns a `BaseChatModel` based on `AI_PROVIDER` |
| `core/agent.py` | `query(user_input) -> str` — builds a fresh agent per call, no shared state |
| `api/main.py` | FastAPI app, CORS, `POST /query`, `GET /health` |
| `data/seed.py` | Drops + recreates `data/company.db` from scratch — run after schema changes |
| `SPEC.md` | Full functional requirements and development phase plan |
| `skills.md` | User-facing capability reference (what queries the agent can answer) |

---

## Database schema (SQLite, `data/company.db`)

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
cp .env.example .env          # set AI_PROVIDER + keys

# Reset database (always run after schema changes)
python3 data/seed.py

# Start API
uvicorn api.main:app --reload

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

### Add a new column to the employees table
1. Add the column to the `CREATE TABLE` statement in `data/seed.py`
2. Add values to every row in the `EMPLOYEES` list — keep the tuple order consistent with the INSERT statement
3. Run `python3 data/seed.py` to recreate the DB
4. Update `SPEC.md` (data model table in FR-2) and `skills.md` if the column enables new query types

### Add a new table
1. Add the `CREATE TABLE` block to `SCHEMA` in `data/seed.py`
2. Add seed data and a `cur.executemany(...)` call in `seed()`
3. Run `python3 data/seed.py`
4. The agent picks it up automatically on next start — unless the table name is in `IGNORED_TABLES`

### Add a new API route
1. Add the route to `api/main.py` only — never touch `core/` for API concerns
2. If the route needs agent logic, call functions from `core/agent.py`

### Change the agent's behaviour / prompt
- Edit the `_PREFIX` string in `core/agent.py`
- The agent is stateless — every call to `query()` builds a fresh agent, so prompt changes take effect immediately on next request

---

## Rules to never break

- `core/` has **zero imports** from `api/` — circular imports will crash the server on startup
- The SQL agent must only issue SELECT statements — the prompt enforces this; don't weaken it
- `salary`, bank details, personal phone numbers, and home addresses must **never** appear in agent responses
- After any schema change to `employees`, update **every row** in `EMPLOYEES` in `seed.py` to include the new column value — SQLite will reject mismatched tuple lengths silently or with a confusing error

---

## Gotchas

- `create_sql_agent` (not `AgentExecutor` + `create_react_agent`) — the latter was removed from `langchain.agents` in v0.3+
- The UI is opened as `file://` so the API has `allow_origins=["*"]` CORS — acceptable for local dev, tighten before production
- `data/seed.py` **drops the database file** on every run — don't run it with data you care about
- The default Ollama model in `config.py` is `llama3.1` — change `OLLAMA_MODEL` in `.env` if you're running a different model locally
