# Prod ERP DB cutover: least-privilege access design

Date: 2026-07-10

## Context

The agent has been running against a dev ERP connection. Moving to the real
prod ERP database. `sql_guard.py` (see `docs/rbac-hardening-roadmap.md`)
already enforces row scope and forbidden columns at the SQL-parsing layer,
but that layer polices an unbounded input space (arbitrary Postgres SQL) and
has already yielded two bypasses in prior review rounds (`CROSS JOIN person`,
`to_jsonb(p)` whole-row smuggling). The roadmap doc itself states: "the
PRIMARY control must be a least-privilege read-only ERP DB role... the
denylist is a backstop, not the boundary." This design makes that primary
control real for the prod cutover.

**Constraint that shapes this design:** the prod ERP database is owned by
another team, not us. We cannot run `ALTER TABLE ... ENABLE ROW LEVEL
SECURITY` or `CREATE POLICY` ourselves (Direction A from the roadmap is
gated on DB ownership, which we don't have). We *can* ask that team to
provision a scoped role and views for us.

## Goal

When the agent points at the real prod ERP, it must be structurally
impossible — not just app-policed — to read tables or columns it doesn't
need, even if `sql_guard.py`'s parser is ever bypassed again. Row/department/
team-level scoping remains the app's responsibility (`sql_guard.py`'s
existing scope injection) since it is per-request, keyed on the requesting
Slack user, and no static DB grant can express it. Everything else — which
tables exist from the agent's perspective, which columns are visible — moves
from "policed by a parser" to "physically absent/ungranted at the DB layer."

## Design

### 1. DB-side: dedicated view schema + scoped role (request to the owning team)

Ask the owning team to provision, once, per environment:

- A new schema, e.g. `hr_agent`, containing **one view per table** the agent
  needs — one `hr_agent.<table>` view per entry in the table list below.
  Each view selects every column from the base table **except** the 15
  columns in `FORBIDDEN_COLUMNS` (`core/rbac/context.py`): salary variants,
  compensation, nic/cnic, bank details, home/personal address, personal
  phone/email, date_of_birth/dob, passport_number, medical_record.
- A new dedicated role, `hr_assistant_ro` (not the currently-shared
  `readonly_user` in `.env` — a role scoped to this app specifically, so
  grant changes for other consumers of the ERP can't silently widen our
  access). Grant it `USAGE` on `hr_agent` and `SELECT` on only the views in
  that schema. **No grants at all** on the base ERP schema/tables.
- Explicit confirmation (not assumption) that this role has no `INSERT` /
  `UPDATE` / `DELETE` / `TRUNCATE` / `EXECUTE` grants anywhere in the
  database. The roadmap flagged that "is the connection actually read-only"
  was previously assumed, never verified.

#### Table list (source: `INCLUDED_TABLES`, `.env`, cross-checked against `sql_guard.py`)

| Table | `sql_guard.py` classification |
|---|---|
| `person` | base table (scoped directly by `_scope_sql`) |
| `person_team` | `_PERSON_FK_TABLES` |
| `leave_record` | `_PERSON_FK_TABLES` |
| `person_week_log` | `_PERSON_FK_TABLES` |
| `person_competency` | `_PERSON_FK_TABLES` |
| `person_skill_category` | `_PERSON_FK_TABLES` |
| `users_personresignation` | `_PERSON_FK_TABLES` |
| `core_personstatushistory` | `_PERSON_FK_TABLES` |
| `core_personemploymenthistory` | `_PERSON_FK_TABLES` |
| `core_personemploymenttypehistory` | `_PERSON_FK_TABLES` |
| `person_leave_limit` | `_PERSON_FK_TABLES` |
| `person_week_project` | `_PERSON_TEAM_FK_TABLES` |
| `annual_review_response` | `_PERSON_TEAM_FK_TABLES` |
| `department` | `_PERSON_FREE_TABLES` |
| `team` | `_PERSON_FREE_TABLES` |
| `designation` | `_PERSON_FREE_TABLES` |
| `employment_type` | `_PERSON_FREE_TABLES` |
| `leave_type` | `_PERSON_FREE_TABLES` |
| `leave_limit` | `_PERSON_FREE_TABLES` |
| `holiday_record` | `_PERSON_FREE_TABLES` |
| `competency_role` | `_PERSON_FREE_TABLES` |
| `competency` | `_PERSON_FREE_TABLES` |
| `competency_level` | `_PERSON_FREE_TABLES` |
| `skill_category` | `_PERSON_FREE_TABLES` |
| `job_requisition` | `_PERSON_FREE_TABLES` |
| `available_time` | **unclassified — pending step 2** |
| `peer_review` | **unclassified — pending step 2** |

25 of these 27 tables are already classified in `sql_guard.py`.
`available_time` and `peer_review` are not (see step 2) — do not include
them in the DBA request until they're either classified there or confirmed
unnecessary and dropped from `INCLUDED_TABLES`. Sending an unclassified
table to the DBA to view/grant would provision access the app can't
currently reason about at the row-scope layer.

This gets both boundaries from one mechanism:
- **Table scope** — the role can only see `hr_agent`'s views; nothing else
  in the ERP schema exists from its perspective.
- **Column scope** — forbidden columns are physically absent from the view
  definition, not merely blocked by `sql_guard`'s column check.

App-side change required: point `DATABASE_URL` at the new role and set the
connection's `search_path` to `hr_agent`. `sql_guard.py` and
`settings.INCLUDED_TABLES` keep referencing bare table names (`person`,
`leave_record`, ...) with **zero code changes** — they resolve to the views
transparently via `search_path`. Row/department/team scope injection in
`sql_guard._inject_scope_into_tree` is unaffected, since it operates on the
same table/alias names regardless of which schema they resolve to.

### 2. App-side: audit and classification fixes (no DBA dependency — start immediately, in parallel with step 1)

- **Resolve the orphan tables.** `available_time` and `peer_review` are
  currently in `INCLUDED_TABLES` (`.env`) but appear nowhere else in the
  codebase or tests, and are not classified in any of `sql_guard.py`'s three
  sets (`_PERSON_FK_TABLES`, `_PERSON_TEAM_FK_TABLES`, `_PERSON_FREE_TABLES`).
  Under the current guard this fails closed for restricted roles (safe, but
  broken for them) — find out why they were added; either classify them
  correctly or drop them from `INCLUDED_TABLES` and the DBA request list in
  step 1.
- **Reconcile `INCLUDED_TABLES` against the real prod schema**
  (`SPEC.md` Phase 1's unchecked item: "Audit production table names; set
  `INCLUDED_TABLES` to only the tables the agent needs"). Confirm every
  table needed for the 20 canonical queries (`SPEC.md`, "Canonical Query
  Test Suite") is present and nothing extra is requested.
- **Add a drift-detection test**: assert every table in
  `settings.INCLUDED_TABLES` appears in exactly one of the three
  `sql_guard.py` classification sets. The roadmap already names this as
  "cheap, high value" — it would have caught the orphan-table issue
  automatically, and prevents recurrence as the table list evolves.

### 3. Pre-cutover verification

- Once `hr_assistant_ro` and the `hr_agent` view schema are provisioned, run
  a raw `psql` check (outside the app, using the new role's credentials
  directly) confirming: forbidden columns are genuinely absent from every
  view's column list; base ERP tables are unreachable
  (`permission denied for schema ...`); no write privileges exist
  (attempt — and expect rejection of — an `INSERT`/`UPDATE` against a view).
- Run the existing `tests/test_scope_execution.py` suite against the new
  role/schema before flipping prod traffic over, to confirm role-scoped
  results still hold through the view layer.

### 4. Rollout

- New prod credentials are handled per `docs/secrets-rotation.md`: stored
  only in the deploy environment, never committed.
- Cut `DATABASE_URL` over to `hr_assistant_ro` only after step 3's checks
  pass. Keep the existing dev DB config available for local development.

## Out of scope (deferred, per roadmap)

- Row-level security (roadmap Direction A in full) — gated on DB ownership
  we don't have; row/dept/team scope remains `sql_guard.py`'s job.
- Typed-tools / semantic-layer approach (roadmap Direction B) — a larger,
  separate architectural change, not needed to safely complete the prod
  cutover.

## Testing

- New: drift-detection test (`INCLUDED_TABLES` vs `sql_guard` classification
  sets) — pure app-side, add alongside `tests/test_rbac.py`.
- New: raw-`psql` verification script/checklist run once against the
  provisioned prod role before cutover (manual or a small throwaway script,
  not part of CI — it needs live prod credentials).
- Existing: `tests/test_scope_execution.py` re-run against the new
  role/schema as a pre-cutover gate.
