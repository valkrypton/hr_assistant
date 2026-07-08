# HR Agent — Refactoring & Security Review Plan

## Implementation Progress

Branch: `refactor/runtime-tools-security-hardening`. Full detail in
`~/.claude/plans/glowing-mixing-twilight.md`. Suite: **362 passing**, ruff clean.

**Decisions locked in:** migrate to LangGraph behind a runtime interface; keep the
four existing roles (`cto_ceo`/`hr_manager`/`dept_head`/`team_lead`) and build a
`policy.can()` layer on top; keep `core/`+`api/`+`adapters/` top-level (no rename
to `hr_agent/`); a security audit ran first and its findings are folded into the
PRs below.

### Done

- [x] **PR1 — Config/security guards + shared persistence helpers** (`6713dd1`)
  - Moved `app_engine`/`erp_engine`/`db_session` → `core/db.py` and `write_audit`
    → `core/telemetry/audit.py` (removes the Slack adapter's duplicated helpers).
  - Audit #3: removed the `APP_DATABASE_URL → DATABASE_URL` cross-fallback.
  - Audit #4/#7: fail-fast prod guards for `ALLOW_UNAUTHENTICATED_QUERY`, unset
    `APP_DATABASE_URL`, and wildcard `CORS_ALLOW_ORIGINS` (all DEBUG-gated).
- [x] **PR2 — Permission policy layer + SQL-guard hardening** (`9c9ff82`)
  - `core/policies/` with `can(subject, action)` / `scope_for()`; re-expressed
    `is_unrestricted` and the sql_guard role dispatch through it (byte-identical
    predicates, proven by the 119 unchanged RBAC oracle tests).
  - Audit #1 (CRITICAL): block SQL-executing/filesystem/DoS functions
    (`query_to_xml*`, `dblink*`, `pg_read_file*`, `pg_sleep*`, …) for all roles.
  - Audit #5: block `SELECT ... INTO`.
- [x] **PR3 — Identity resolver + AgentContext** (`6e3f133`)
  - `core/errors.py` (typed errors), `core/identity/` with `AgentContext`
    (wraps RBACContext) and `resolve()`/`lookup_hr_user()` consolidating the
    duplicated HRUser lookup.
- [x] **PR4 — Runtime interface + execution pipeline** (`0450be5`)
  - `core/runtimes/` (`AgentRuntime` protocol, `AgentRunRequest`/`Result`,
    `LegacyRuntime`, `get_runtime()` on `AGENT_RUNTIME`, default `legacy`).
  - `core/execution.run_query()` pipeline: rate-limit → identity → runtime →
    redact → audit. Redaction moved out of `agent.query()` into the pipeline /
    Slack orchestration (single enforcement point). `query_service` is now a
    thin error→HTTP mapper.
- [x] **PR5 — Tool registry + guarded SQL tool** (`88abdc8`, `eeab812`)
  - `core/tools/`: `Tool` (enforces `required_permissions` in the tool layer),
    `registry.for_context()`, and `query_erp_sql` (guarded free-form SQL on the
    read-only ERP engine + `SqlCollector` telemetry). Additive; not yet on a
    live path.

- [x] **PR6 — LangGraph runtime** *(default stays `legacy`)* (`9efba64`, `f904344`)
  - `core/runtimes/langgraph/` via `langchain.agents.create_agent`; ported prompt,
    ctx-bound tools (scope from context, never LLM args), `usage_metadata` token
    counting, `SqlCollector` telemetry, `hr_records` probed directly (fixes the
    legacy restricted-role misreport). `scripts/compare_runtimes.py` golden harness
    over the 20 canonical queries. Structurally verified with a fake tool-calling
    model. **Still open:** run the golden comparison with a live LLM provider and
    review it before flipping the default in PR7.

### Remaining

- [ ] **PR7 — Cutover + legacy removal** — flip default to `langgraph`; delete
  the legacy agent internals and `langchain-community`; add `test_architecture.py`
  enforcing the import boundaries.
- [ ] **PR8 — First typed tools** — `team_roster` (#8), `leave_lookup` (#12),
  `joiners_summary` (#13/#14/#15).
- [ ] **PR9 — Observability + audit integrity** — audit columns
  (`tools_used`/`sql_statements`/`model_name`/`rows_returned`) via Alembic;
  audit #6 append-only enforcement (REVOKE UPDATE/DELETE) + SQLAdmin review.
- [ ] **PR10 — Test-gap closure, guardrails, docs** — mis-listed-table leak test,
  Slack `event_id` dedupe (audit #8), doc + diagram updates.

**Infra (not code), tracked alongside PR1:** provision a least-privilege
read-only ERP DB role (SELECT-only on specific tables, no dangerous functions) —
collapses audit #1/#3/#5 from exploitable to defense-in-depth.

---

## Current principles that must remain

Do not weaken these security guarantees:

- Identity must be resolved before any LLM call.
- Unregistered users must be rejected without invoking the model.
- Employees must never have direct SQL access.
- HR SQL queries must pass through SQL validation.
- Database access must remain read-only.
- All requests and tool usage must be audited.

## Phase 1: Analyze Current Architecture

First inspect the repository and provide:

- Current architecture diagram
- Existing modules and responsibilities
- Problems with current separation of concerns
- Places where LangGraph-specific code is mixed with business logic
- Security risks or missing controls

**Do not modify code yet.**

## Phase 2: Introduce Core Abstractions

Refactor toward this structure:

```
hr_agent/
├── core/
│   ├── context.py
│   ├── execution.py
│   └── registry.py
├── identity/
│   ├── resolver.py
│   └── roles.py
├── policies/
│   ├── permissions.py
│   └── evaluator.py
├── tools/
│   ├── employee/
│   ├── hr/
│   └── shared/
├── database/
│   ├── erp.py
│   └── app.py
├── security/
│   ├── sql_guard.py
│   └── output_filter.py
├── runtimes/
│   └── langgraph/
├── integrations/
│   └── slack.py
└── telemetry/
    └── audit.py
```

Keep the implementation simple. Do not over-engineer.

## Phase 3: Tool-First Architecture

Move business capabilities into tools.

Every tool should have:

- name
- description
- input schema
- output schema
- required permissions

Example:

```python
class EmployeeLeaveBalanceTool:
    permissions = ["employee.leave.read"]

    async def execute(self, context, employee_id):
        ...
```

- The agent should decide which tool to call.
- The tool itself must enforce authorization.
- **Never rely on prompts.**

## Phase 4: Add Permission Policy Layer

Replace scattered role checks.

Avoid:

```python
if user.role == "hr":
```

Create:

```python
policy.can(user, action="employee.salary.read")
```

Support these roles:

- `employee`
- `hr_manager`
- `admin`

Make permissions configurable.

## Phase 5: LangGraph Isolation

Move all LangGraph-specific code into `runtimes/langgraph/`.

The rest of the application should not import LangGraph.

The goal:

```
Business Logic
      |
Agent Runtime Interface
      |
LangGraph Implementation
```

Later we should be able to add another runtime without rewriting tools.

## Phase 6: Improve Agent Flow

Current flow:

```
Slack → Agent → Tools
```

Change to:

```
Slack
  |
Identity Resolution
  |
Rate Limit
  |
Create Agent Context
  |
Agent Runtime
  |
Tool Execution
  |
Security Validation
  |
Audit
  |
Response
```

## Phase 7: Add Agent Context Object

Create a shared context:

```python
AgentContext:
    user_id
    slack_id
    role
    permissions
    request_id
    metadata
```

Every tool receives this context.

## Phase 8: Add Better Observability

Audit each request with:

- user
- question
- selected tool
- execution time
- SQL generated
- rows returned
- errors
- model name

Do not store sensitive data unnecessarily.

## Phase 9: Testing

### Security

- unknown Slack user cannot call LLM
- employee cannot execute SQL
- employee cannot access another employee's data
- HR can execute allowed queries
- forbidden columns are blocked
- write queries are blocked

### Tools

- Each tool should have permission tests.

### Runtime

- LangGraph workflow should have integration tests.

## Phase 10: Keep It Simple

Do not add:

- unnecessary abstractions
- custom agent framework
- custom memory implementation
- custom workflow engine

Use LangGraph features where they provide value.

The goal is:

> "Thin application code + strong security boundaries + framework-provided agent orchestration."

After implementation, provide:

- Changed files
- Architecture diagram
- Security improvements
- Remaining technical debt
- Suggested next steps

---

# Security Audit

Perform a security audit of this HR Agent application.

Act as a principal security engineer reviewing an enterprise AI assistant.

## Check

- Can an employee access another employee's data?
- Can prompt injection bypass permissions?
- Can the LLM execute unsafe SQL?
- Can tools be called without authorization?
- Can Slack identity be spoofed?
- Can audit logs be modified?
- Can sensitive HR data leak through generated responses?

## Review

- authentication flow
- authorization flow
- tool permissions
- SQL guard
- database permissions
- output filtering
- logging

## For every issue provide

- severity
- exploit scenario
- affected file
- recommended fix
- test case to prevent regression

Do not only review prompts. Assume attackers know how LLM agents work.
