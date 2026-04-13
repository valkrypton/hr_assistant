# HR Intelligence Agent — Specification

Natural-language workforce assistant accessible via Slack,
backed by a hybrid SQL + AI-search engine against the company ERP.

---

## Functional Requirements

### FR-1  Query Interface

| ID | Requirement |
|----|-------------|
| FR-1.1 | Accept free-text questions in English via Slack (DM or `@hr-agent` mention in any channel) |
| FR-1.2 | Return answers within 5 seconds for SQL-path queries, 15 seconds for hybrid queries |
| FR-1.3 | Format responses as rich Slack Block Kit cards with action buttons |
| FR-1.4 | Support threaded replies in Slack (reply inside the thread of the original mention) |
| FR-1.5 | Gracefully handle ambiguous queries by asking a single clarifying question before proceeding |

---

### FR-2  Data Sources

| ID | Requirement |
|----|-------------|
| FR-2.1 | Connect to the company PostgreSQL ERP as the primary structured data source |
| FR-2.2 | Read from: `employees`, `departments`, `teams`, `skills`, `projects`, `daily_logs`, `billable_status`, `leaves`, `hr_records` (warnings/exits) |
| FR-2.3 | Index free-text content (project descriptions, daily log entries) into a vector store for semantic search |
| FR-2.4 | Keep the vector index in sync with new daily log and project entries (nightly re-index minimum) |
| FR-2.5 | When an `hr_records` table is absent, surface operational signals (log gaps, utilization drops) as proxy indicators and explicitly state the limitation in the response |

#### Employee data model — key columns

| Column | Values / notes |
|--------|---------------|
| `employment_type` | `full_time` \| `part_time` → classified as **employed**; `contractor` → classified as **subcontractor** |
| `competency_role` | Canonical role bucket independent of job title — e.g. `Software Engineer`, `QA Engineer`, `Product Manager`, `DevOps Engineer`, `UI/UX Designer`, `HR Specialist`, `Financial Analyst`, `Sales Executive`, `Marketing Specialist` |
| `competency_score` | Float 0–100; assessed independently of the annual performance rating |
| `exit_date` | ISO-8601 date; NULL while employed |
| `exit_type` | `resignation` \| `termination`; NULL while employed |
| `status` | `active` \| `on_leave` \| `terminated` |

---

### FR-3  Query Engine — SQL Path (≈80% of queries)

| ID | Requirement |
|----|-------------|
| FR-3.1 | Translate natural-language questions into SQL SELECT statements; never execute INSERT / UPDATE / DELETE / DROP |
| FR-3.2 | Support compliance queries: identify employees with missing or incomplete daily logs for a given date range, automatically excluding approved leave days |
| FR-3.3 | Support log-hour queries: calculate per-employee average logged hours per day for a configurable period; group results by severity (e.g. < 6 hrs, 6–7.5 hrs) |
| FR-3.4 | Support attrition queries: filter employees by exit status and departure date; enrich results with tenure, skills, and last project |
| FR-3.5 | Support availability queries: combine skill filter + leave calendar + current utilisation percentage to return a ranked list of available employees |
| FR-3.6 | Support bench queries: identify employees with continuous non-billable status beyond a configurable threshold (default: 30 days); include skills and last project |
| FR-3.7 | Support team overview queries: return full roster for a department/team with current project, utilisation %, billable status, and leave state |
| FR-3.8 | Support individual employee lookups: status, tenure, skills, current assignment, leave balance, utilisation history |
| FR-3.9 | Support aggregation queries: attrition count by team, average utilisation by department, headcount by skill |
| FR-3.10 | **New joiner queries** — count employees hired in a given year; break down by employment classification: *employed* (full_time \| part_time) vs *subcontractor* (contractor) |
| FR-3.11 | **Same-year cohort attrition** — count (and list) employees who both joined and exited within the same calendar year |
| FR-3.12 | **Resignation breakdown by department** — count `exit_type = 'resignation'` records grouped by department for any user-specified time window |
| FR-3.13 | **Resignation breakdown by years-of-experience bracket** — calculate each resigned employee's tenure at exit (`exit_date − hire_date`); group into brackets (e.g. 0–1, 1–2, 2–3 yrs); bracket size and upper bound must honour whatever the user specifies in the query, defaulting to 1-year intervals |
| FR-3.14 | **Termination count by year** — count `exit_type = 'termination'` records grouped by exit year |
| FR-3.15 | **Competency role headcount** — count active employees grouped by `competency_role`; support filtering to a specific role (e.g. "how many Software Engineers do we have?") |
| FR-3.16 | **Individual competency score lookup** — return a named employee's `competency_score` together with their `competency_role` and department for context |

---

### FR-4  Query Engine — AI Search Path (≈20% of queries)

| ID | Requirement |
|----|-------------|
| FR-4.1 | Use semantic vector search over project descriptions and daily log entries to answer discovery questions (e.g. "who has Sabre API experience?") |
| FR-4.2 | Clearly state in the response when a skill was found via text search rather than a structured skill tag |
| FR-4.3 | Support hybrid queries that combine SQL filters (skill tag, availability, utilisation) with semantic search (domain/technology experience) — merge and rank results from both paths |
| FR-4.4 | Score and surface the top-N matches with a brief evidence excerpt from the source text |

---

### FR-5  Role-Based Access Control

| ID | Requirement |
|----|-------------|
| FR-5.1 | Identify the requesting user from their Slack identity |
| FR-5.2 | Enforce four roles: **CTO/CEO**, **HR Manager**, **Department Head**, **Team Lead** |
| FR-5.3 | CTO/CEO — full company-wide access: all workforce data, utilisation, bench time, attrition |
| FR-5.4 | HR Manager — company-wide: employee details, skills, availability, leaves, utilisation, warnings, attrition |
| FR-5.5 | Department Head — own department + cross-department reads: roster, skills, availability, utilisation, project history |
| FR-5.6 | Team Lead — own team only: member names, skills, availability, current projects |
| FR-5.7 | Silently restrict out-of-scope queries (return only data the requester is authorised to see, without disclosing that data was withheld) |
| FR-5.8 | **Never expose** regardless of role: salary figures, bank details, personal addresses, personal phone numbers, medical records |

---

### FR-6  Audit & Compliance

| ID | Requirement |
|----|-------------|
| FR-6.1 | Log every query with: requesting user identity, channel (Slack), timestamp, raw question, data tables accessed, and row count returned |
| FR-6.2 | Store audit logs in an append-only table; do not delete or update entries |
| FR-6.3 | Expose a `/audit` API endpoint for admin-level log retrieval (date range, user, table filters) |

---

### FR-7  Administration

| ID | Requirement |
|----|-------------|
| FR-7.1 | Provide a CLI or admin API endpoint to register/deregister Slack users and map them to employee roles |
| FR-7.2 | Require `INCLUDED_TABLES` configuration — an explicit whitelist of tables the agent may query; all other tables are invisible to the agent |
| FR-7.3 | Support configurable thresholds via environment variables: bench duration, log-hour threshold, utilisation warning level |

---

## Non-Functional Requirements

| ID | Requirement |
|----|-------------|
| NFR-1 | All queries must be answered without mutating any data source |
| NFR-2 | The system must handle at least 200 queries per day on the target AI model (GPT-4o-mini or equivalent) |
| NFR-3 | No new infrastructure required beyond the AI API subscription; deploy on existing servers |
| NFR-4 | AI model is swappable via `AI_PROVIDER` env var (Ollama, OpenAI, Anthropic, xAI, QWEN) without code changes |
| NFR-5 | The `core/` package has zero dependency on the `api/` or messaging-adapter packages |
| NFR-6 | Target AI cost: $25–50/month |

---

## Development Phases

### Phase 0 — Foundation  *(complete)*
> Goal: working local prototype with web UI

- [x] Project scaffold (`core/` + `api/` separation, no circular imports)
- [x] SQLite seed database — 7 tables, 32 employees with realistic exit, competency, and joiner data
- [x] Extended `employees` schema: `competency_role`, `competency_score`, `exit_date`, `exit_type`
- [x] LangChain SQL agent wired to the database (`core/agent.py`) via `create_sql_agent`
- [x] Multi-provider LLM factory (`AI_PROVIDER` env var — Ollama default, OpenAI, Anthropic, xAI, QWEN)
- [x] FastAPI `POST /query` + `GET /health` with CORS for `file://` origin
- [x] `index.html` web UI with example query chips, textarea, Ctrl+Enter shortcut
- [x] `INCLUDED_TABLES` whitelist — agent only sees explicitly listed tables
- [x] Agent prompt explicitly blocks salary and personal data from all responses
- [x] `.gitignore` covering `.venv`, `data/company.db`, `__pycache__`, `.env`
- [x] `CLAUDE.md`, `SPEC.md`, `skills.md` documentation
- [x] `.claude/skills/hr-assistant/SKILL.md` project-local development skill

---

### Phase 1 — Production Data Layer  *(in progress)*
> Goal: connect to real ERP, replace SQLite prototype with PostgreSQL

**Tasks**
- [x] Add `psycopg2-binary` to requirements
- [x] `DATABASE_URL` now defaults to PostgreSQL format in `.env.example`
- [x] `GET /health` performs a real DB connectivity check (returns 503 if unreachable)
- [ ] Set `DATABASE_URL` in `.env` to the production connection string
- [ ] Audit production table names; set `INCLUDED_TABLES` to only the tables the agent needs
- [ ] Map ERP column names to agent expectations — add DB views if names differ significantly
- [ ] Implement `hr_records` fallback: detect when the table is absent and surface operational-signal proxies
- [ ] Add nightly vector-index job: chunk project descriptions + daily log entries → embed → store in pgvector or Chroma

**Exit criteria:** Agent answers all FR-3 and FR-4 query types against live ERP data with correct results.

---

### Phase 2 — Role-Based Access Control
> Goal: every answer is scoped to the requester's permissions before it leaves the system

**Tasks**
- [ ] Create `users` table mapping employee ID → role → Slack user ID
- [ ] Implement `RBACContext` middleware in `core/`: injects allowed department/team scope into every SQL query and strips forbidden columns from results
- [ ] Add role enforcement to the SQL agent prompt: inject a system prefix describing what the current user may and may not see
- [ ] Write tests covering each role boundary (e.g. Team Lead cannot see another team's roster)
- [ ] Implement the `/audit` log table and endpoint (FR-6)

**Exit criteria:** The same question asked by a Team Lead and an HR Manager returns correctly scoped results; salary/personal fields never appear in any response.

---

### Phase 3 — Slack Integration
> Goal: `@hr-agent` in any channel or DM returns a rich answer in-thread

**Tasks**
- [x] Create Slack App with Bot Token + Event Subscriptions (`app_mention`, `message.im`)
- [x] Implement `adapters/slack.py`: event handler, URL verification challenge, extract user ID + message text
- [x] Map Slack user ID → employee role via the `users` table
- [x] Call `core.agent.query()` with RBAC context; format response as Slack Block Kit (header, body text, optional action buttons for follow-up queries)
- [x] Post reply inside the original message thread (not as a new top-level message)
- [x] Add `POST /webhook/slack` route; verify `X-Slack-Signature` on every request
- [x] Create `#ask-hr` channel setup guide for admins (`docs/slack-setup-guide.md`)

**Exit criteria:** `@hr-agent Who is on the bench right now?` in any Slack channel returns a rich card scoped to the requester's role, replied in-thread.

---

### Phase 4 — Hardening & Observability
> Goal: production-ready reliability, cost tracking, and admin visibility

**Tasks**
- [ ] Add query latency logging (SQL path vs AI-search path, total round-trip)
- [ ] Implement per-user rate limiting (configurable, default 30 queries/hour)
- [ ] Add AI token usage tracking per query; surface monthly cost estimate in the admin dashboard
- [ ] Retry logic and graceful degradation: if the AI call fails, return a user-friendly error rather than a stack trace
- [ ] End-to-end test suite covering the 12 canonical query types from the proposal
- [ ] Load test: verify 200 queries/day throughput target (NFR-2)
- [ ] Secrets rotation guide: how to rotate Slack and AI API keys without downtime

**Exit criteria:** System passes load test, all canonical queries return correct results, audit log is queryable by admins.

---

## Canonical Query Test Suite

These queries must return correct, role-scoped answers at the end of Phase 4:

| # | Query | Primary path |
|---|-------|-------------|
| 1 | "Who hasn't filled their daily logs this week?" | SQL |
| 2 | "Who's not adding full 8 hours in their daily logs?" | SQL |
| 3 | "Who got warnings in the last quarter?" | SQL / proxy |
| 4 | "Any devs who resigned recently?" | SQL |
| 5 | "Who is not performing well on the backend team?" | SQL proxy |
| 6 | "Who's available for a Django project starting May?" | SQL |
| 7 | "Who's been non-billable for the last 2 months?" | SQL |
| 8 | "Show me the backend team right now" | SQL |
| 9 | "Who has experience with Sabre APIs?" | AI search |
| 10 | "Find React devs with e-commerce experience available in May" | Hybrid |
| 11 | "Which team has the most attrition this year?" | SQL |
| 12 | "Who's on leave next week?" | SQL |
| 13 | "How many new joiners did we have in 2025?" | SQL |
| 14 | "Of the 2025 joiners, how many were employees and how many subcontractors?" | SQL |
| 15 | "How many people who joined in 2025 also left in 2025?" | SQL |
| 16 | "Break down all resignations by department" | SQL |
| 17 | "Show resignations by years of experience — use 1-year brackets" | SQL |
| 18 | "How many terminations did we have in 2023?" | SQL |
| 19 | "How many Software Engineers, QA Engineers, and Product Managers do we have?" | SQL |
| 20 | "What is Bilal Qureshi's competency score?" | SQL |
