# RBAC Hardening & Architecture Roadmap

Future directions for how the HR agent enforces access control. Nothing here is
committed work — it records the options, tradeoffs, and prerequisites so the
decision can be made deliberately later. Written after two rounds of security
review of the SQL-guard approach (see `core/rbac/sql_guard.py`).

## Where we are today

RBAC is enforced in the **application layer**: every SQL statement the LLM
generates passes through `rewrite_sql()` (`core/rbac/sql_guard.py`) before
execution. It parses the SQL with sqlglot, injects scope predicates
(`department_id = …`, `person_id IN (…)`), blocks forbidden columns
(salary/CNIC/DOB), rejects non-SELECT and wildcard/whole-row projections, and
fails closed on unclassified tables.

This works and is now covered by **execution-based tests**
(`tests/test_scope_execution.py`) that seed data and assert a restricted role
can only retrieve in-scope rows — not just that the rewritten SQL *looks* right.

### Why we may want to move enforcement elsewhere

The current model polices an **unbounded input space** (all valid Postgres) with
a parser-based guard. That is inherently hard to win:

- Two review rounds found bypasses that produced plausible SQL and passed the
  text-based tests (`CROSS JOIN person` disabling FK-table scope; `to_jsonb(p)`
  smuggling every column). Each was closed, but the surface keeps yielding new
  syntax to probe.
- The `_PERSON_FREE_TABLES` / `_PERSON_FK_TABLES` allowlists are hand-maintained.
  Fail-closed protects *unlisted* tables, but a *mis-listed* one leaks silently.
- The guard emits Postgres dialect; SQLite tests can't validate Postgres-only
  parse/emit edge cases (lateral joins, `DISTINCT ON`, window functions, etc.).

None of these block the current approach, but they are the reason the two
directions below exist.

---

## Direction A — Push enforcement into Postgres (RLS + column views)

Keep the LLM writing free-form SQL, but make the **database** enforce scope, so
no query shape can escape it.

### Sketch

```sql
ALTER TABLE person ENABLE ROW LEVEL SECURITY;
ALTER TABLE person FORCE ROW LEVEL SECURITY;   -- owner is filtered too

CREATE POLICY person_scope ON person USING (
    current_setting('hr.role', true) IN ('cto_ceo','hr_manager')
 OR (current_setting('hr.role', true) = 'dept_head'
     AND department_id = current_setting('hr.dept_id', true)::int)
 OR (current_setting('hr.role', true) = 'team_lead'
     AND id IN (SELECT person_id FROM person_team
                WHERE nsubteam_id = current_setting('hr.team_id', true)::int
                  AND end_date IS NULL AND is_active))
);
-- + one policy per person_id / person_team_id linked table (same logic
--   sql_guard injects today, expressed once as a policy).
```

- **Forbidden columns** → a view (`person_v`) that omits salary/CNIC/DOB; grant
  the app role only the view. RLS on the base table propagates through it. Point
  `INCLUDED_TABLES` at the view.
- The `rewrite_sql` scope-injection layer becomes unnecessary for row scope
  (keep the non-SELECT block as cheap defense-in-depth).

### Prerequisites — confirm before starting

1. **Do we own/administer the ERP database?** RLS needs `ALTER TABLE` /
   `CREATE POLICY` DDL on the ERP. If `DATABASE_URL` points at a vendor ERP or a
   replica another team owns, this requires a dedicated replica we control.
   **This gate decides feasibility.**

### Safety-critical implementation details

2. **`SET LOCAL`, never `SET`.** The scope session variable must be
   transaction-scoped. LangChain's `SQLDatabase` pools connections; a plain
   `SET hr.dept_id='3'` would persist on the connection and leak that scope to
   the next user who reuses it. `SET LOCAL … ; <query>` must run in one
   transaction (SQLAlchemy connection-event hook or a run-wrapper override).
   This is the single most dangerous part.
3. **App connects as a plain, non-owner role.** RLS is bypassed by
   superusers/owners unless `FORCE ROW LEVEL SECURITY` is set.

### Why it's low-risk to try

The execution-based test suite is the oracle: the same assertions ("dept_head
sees only dept 3", the CROSS JOIN case, inactive-membership exclusion) validate
RLS too — just enforced by Postgres. RLS tests need a real Postgres fixture
(SQLite has no RLS).

---

## Direction B — Constrain generation: typed tools / semantic layer

Don't let the LLM write raw SQL at all. It calls parameterized functions and
picks *which* tool with *which* params; it never writes a `WHERE` clause.

### Sketch

```python
def find_people(ctx: RBACContext, *, skills=None, available_after=None,
                team=None, min_experience_years=None) -> list[PersonCard]:
    # scope comes from ctx (the authenticated request), never from LLM input.
    # PersonCard has name + dept only — salary/CNIC/DOB cannot appear.
```

The LLM emits `find_people(skills=["React","e-commerce"], available_after="2025-05-01")`.
No `WHERE`, no join to abuse — the CROSS JOIN / whole-row bug classes stop
existing. Scope and forbidden columns become **structural**.

### The double win

Beyond security, this fixes a **correctness** problem. The SPEC's
"NON-DISCOVERABLE BUSINESS RULES" prompt block (status IDs, "approved leave =
status 1", the `hr_records` proxies) is business logic smuggled into a prompt,
hoping the LLM applies it each time. Typed tools encode those rules once, in
tested code, instead of re-deriving them per query.

### The tradeoff

A **capability ceiling**: fixed tools answer only what's encoded. The aggregate
questions (headcount, attrition, joiners-by-year) map perfectly; exploratory
ones need richly-parameterized tools. The mitigation is a **hybrid**: a handful
of aggregate tools + one flexible `find_people(structured filters)` where the
filters are an allowlist of dimensions/operators compiled to SQL server-side —
expressive, still not free SQL. The SPEC's ~20 fixed questions make this a
strong fit.

### Model options (from most to least locked-down)

| Model | Flexibility | Design cost |
|---|---|---|
| Fixed function per question | lowest | lowest |
| Hybrid (aggregate tools + `find_people` spec) | medium | medium |
| Full semantic query-spec (`{metric, dimensions, filters[]}`) | highest | highest |

---

## Testing evolution

- **Done:** execution-based scope tests (`tests/test_scope_execution.py`) — seed
  data, run through the guard, assert on rows.
- **Next (cheap, high value):** a "mis-listed table" test — seed a person-bearing
  table wrongly placed in the person-free set and assert it's caught, closing the
  last silent-leak path in the current model.
- **Property-based (Hypothesis):** generate random SELECTs over person-bearing
  tables; assert the result is always scoped or the query rejected — catches the
  class enumerated tests miss.
- **Postgres pass:** run scope tests against real Postgres to cover dialect edge
  cases (and to validate Direction A if pursued).

---

## Open decisions

- **A vs B vs keep-and-harden.** A keeps LLM flexibility and moves enforcement to
  the engine; B trades some flexibility for structural safety and better
  correctness on fuzzy questions; keeping the current guard is viable but is the
  hardest place to enforce and needs ongoing adversarial testing.
- **ERP database ownership** — gates Direction A entirely.
- **Config to confirm regardless of direction:** is the ERP connection role
  actually read-only via a DB `GRANT` (not just intended)? The guard should be
  defense-in-depth, not the sole write protection.
- **Slack channel-thread history** currently drops assistant turns in shared
  channels to prevent cross-scope leaks — a UX tradeoff that wants product
  sign-off or a proper reply-parent design.
