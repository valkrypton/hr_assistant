# Deterministic RBAC from ERP Group Membership

**Date:** 2026-07-27
**Branch:** `feat/deterministic-rbac`
**Status:** Implemented

## Problem

Access control today is split across three mechanisms, only one of which is enforceable:

1. `hr_assistant_users.role` / `department_id` / `team_id` — hand-maintained rows. Nothing keeps them in sync with the ERP. A promotion, transfer, or departure silently leaves stale authorization behind.
2. `core/rbac/prompt.py` — a natural-language scope block injected into the agent prefix, asking the LLM to restrict its own SQL. Not a boundary. Prompt injection defeats it, and so does an ordinary model mistake.
3. `core/rbac/sql_guard.py` — deterministic SQL rewriting at the `db.run()` call site. This one is real, but it is driven by the role values from (1).

The goal is to delete (1) and (2) entirely: derive every authorization decision from the ERP at login time, and leave `sql_guard` as the sole enforcement point.

## Investigation summary

The ERP is a Django application. Its authorization data lives in the standard `auth_*` tables, read here from a local copy at `postgresql://ali.tariq@localhost:5432/hrdb`.

### What Django's permission model can and cannot express

`auth_permission` holds `(content_type_id, codename)` pairs — a verb applied to a *model*. Django auto-creates `add_`/`change_`/`delete_`/`view_` per model on every `migrate`; applications add free-form custom codenames via `Meta.permissions`. Grants reach a user two ways, unioned with no precedence and no deny:

```
effective_perms(user) = direct grants ∪ ⋃ perms(g) for g in groups(user)
```

Measured in this database: 1,865 permissions across 32 apps, 726 users, 109,618 direct grants, 98 groups, 5,012 memberships.

Three properties determine the design:

- **No row scope.** `view_person` means "may read the Person model", never "may read these rows". Django's own answer to per-row rules is Python code in view `get_queryset()` overrides — logic that exists only in ERP source, not in any table we can read.
- **No column scope.** Nothing below model granularity. `person_compensation_report` is a custom codename that ERP view code *interprets* as "render the salary column"; the row itself carries no such meaning.
- **No deny.** Grants only. Restrictions can only be expressed as absence-of-grant.

Consequently `auth_permission` can answer *"may this user read table T"* and nothing else. Row scope must come from elsewhere.

### Data hazards found (dev copy)

This is a scrambled dev database. The following are recorded because they shaped the design, and because several must be re-verified against production.

- **`change_` without `view_`.** 88 users hold `change_person`, 69 hold `view_person`, and 19 hold change but not view. Pre-Django-2.1 groups were written when `change_` implied read — e.g. `Salary Review Group` grants add/change/delete on `core.person` and no `view_person`. Any permission-based gate must test `view_X OR change_X`. *(Not load-bearing in the final design, which reads no codenames — retained as a hazard note for any future extension.)*
- **Group name collisions.** 28 groups match `pod` or `manage`: `Pod` vs `Advanced POD Permissions` vs `POD Reminder Group`; `Management` vs `Management Permissions` vs `Leave Management`. Fuzzy or case-insensitive matching would grant unrestricted access to the wrong population.
- **`Pod` and `Management` carry almost no permissions** — 1 and 0 respectively. They are membership markers, not permission bundles. This is fine for this design, which uses them as labels only, and is recorded so nobody later "fixes" it by reading their permission sets.
- **Membership is noise in this dump.** 65 users in `Pod` + 64 in `Management` = 67 distinct, so 62 are in both. `Pod` membership by department is 30 Engineering, 15 QA, 5 Accounts, 3 People Ops & Dev — the inverse of what an HR group should look like. Only 120 of 726 users belong to any group, yet those 120 average 41.8 memberships, one holding all 98.
- **Reference tables are dirty.** Duplicate HR departments (`People Ops & Dev` id 24, `People operation & Development` id 37); junk rows (`asde`, `newwsscnmmcmmnnmnmcmn23`, blank name); duplicate typo'd groups (`Project Logs Invoices` / `pRoject Logs Invoices`).
- **40 superusers** (5.5% of accounts), scattered across departments.

Production is asserted to be clean. The design still fails closed on these conditions, because the cost is a few lines and the failure mode is silent privilege escalation.

### Approaches considered

**When scope is resolved** — chose resolve-at-login with a TTL cache (one ERP round trip per session, revocation window bounded by TTL) over per-request resolution (3 extra ERP queries on every message, including Slack) and over syncing perms into the app DB (reintroduces the hand-maintained state this work exists to delete).

**How the table gate is enforced** — considered a per-request `INCLUDED_TABLES` narrowed to what the user may reach, enforced by `sql_guard` behind it. This axis turns out to be **degenerate under the chosen two-level model**: `UNRESTRICTED` and `SELF` see the same table list and differ only in row scope. `INCLUDED_TABLES` therefore stays static and process-wide, exactly as today. The option is recorded because it becomes live again if per-table gating is ever revisited (see *Out of scope*).

## Design

### Access levels

```python
class AccessLevel(str, Enum):
    UNRESTRICTED = "unrestricted"   # member of Pod (HR) or Management
    SELF         = "self"           # everyone else
```

Two levels, resolved from ERP group membership. `Role` (4 values) and `ScopePromptBuilder` are deleted.

### Login resolution

```
SSO email
  → auth_user (id, is_active)          ERP, read-only
  → person    (id, is_active)          ERP, via person.user_id
  → auth_user_groups ∩ {HR_GROUP_ID, MANAGEMENT_GROUP_ID}
  → AccessLevel + person_id
```

Three indexed reads. Result cached per session for `RBAC_CACHE_TTL_SECONDS`.

### Request path

```
HTTP /query                          Slack event
  session cookie                       slack_user_id
       |                                    |
  cached ErpIdentity              hr_assistant_users -> employee_id
       | (miss / TTL expired)               |
       +------------> ErpIdentityResolver <-+
                          |
                    AccessLevel.resolve()
                          |
                    ScopePolicy(level, person_id)
                     |                    |
              scope_hint()           rewrite_sql()   <- the only boundary
              (advisory)                  |
                     |                    |
                  agent prefix       db.run()
```

`INCLUDED_TABLES` remains static and process-wide — both access levels see the same tables and differ only in row scope.

`HRUser` survives in reduced form. Slack has no SSO, so the row remains the only `slack_user_id -> employee_id` mapping. It keeps `employee_id`, `slack_user_id`, `is_active`, and loses `role`, `department_id`, `team_id`. Slack resolves identity by `employee_id -> person.user_id -> groups`; HTTP resolves by `email -> auth_user`. Both converge on the same `ErpIdentity` before any authorization decision.

### Components

| Module | Change | Responsibility |
|---|---|---|
| `core/rbac/erp_identity.py` | new | `ErpIdentity = (auth_user_id, person_id, group_ids)`. The only module touching ERP auth tables. |
| `core/rbac/access.py` | new | `AccessLevel` enum + `resolve(identity, settings) -> AccessLevel`. Pure, no I/O. |
| `core/rbac/policy.py` | rewrite | `ScopePolicy(access_level, person_id)`. `is_unrestricted` keeps its name — `sql_guard` already branches on it. `can_see_employee` becomes a `person_id` equality check. |
| `core/rbac/sql_guard.py` | edit | `_scope_sql` / `_scoped_person_ids_sql` lose their `dept_head` / `team_lead` branches and gain one self predicate. |
| `core/rbac/context.py` | shrink | Façade retained so existing call sites keep compiling. `for_user` takes an `ErpIdentity`. |
| `core/rbac/prompt.py` | replace | `ScopePromptBuilder` (56 lines) becomes `scope_hint()` (~10 lines). |
| `core/rbac/roles.py` | delete | |

Unchanged in `sql_guard`: DML/DDL blocking, the `_BLOCKED_FUNCTIONS` denylist, `SELECT ... INTO` rejection, forbidden-column blocking, wildcard and whole-row rejection, table classification, `assert_tables_classified`, `assert_person_free_tables_have_no_person_fk`, and `_inject_and`'s paren-wrapping.

### The scope prompt becomes advisory

Removing the scope prompt outright makes self-scoped results look silently wrong — "how many people in Engineering" returns `1` with no explanation. `scope_hint()` returns one sentence ("You can only see your own records"), documented in its module docstring as **advisory only, carrying no authorization weight**. The forbidden-columns block stays in the prompt on the same footing: a UX nicety layered over a hard SQL-layer block that already exists.

### The predicate

`UNRESTRICTED` — no injection. `rewrite_sql`'s existing `restricted` flag short-circuits, giving byte-identical behaviour to today's `HR_MANAGER`. Still subject to DML blocking, the function denylist, forbidden columns, and wildcard rejection, which apply to every caller including a `None` context.

`SELF`, requester `person_id = X`:

| Table class | Injected predicate |
|---|---|
| `person` | `{alias}.id = X` |
| `_PERSON_FK_TABLES` (11 tables) | `{alias}.person_id = X` |
| `_PERSON_TEAM_FK_TABLES` (2 tables) | `{alias}.person_team_id IN (SELECT id FROM person_team WHERE person_id = X)` |
| `_PERSON_FREE_TABLES` (12 tables) | none — lookup data, readable by all |
| unclassified | `ValueError`, fail closed (unchanged) |

Strictly simpler than the current implementation: `_scoped_person_ids_sql` collapses from two role branches with correlated subqueries to a single integer literal, and the `nsubteam_id` / `person_team` scope subqueries delete outright.

### Configuration

```python
HR_GROUP_ID: int = 12            # "Pod"
MANAGEMENT_GROUP_ID: int = 13    # "Management"
RBAC_CACHE_TTL_SECONDS: int = 900
```

IDs, not names. A startup assertion verifies both IDs exist and their names still read `Pod` and `Management`; a mismatch raises at boot, following the existing `assert_tables_classified` pattern. Production is expected to be clean; this is six lines and it is the difference between a group rename becoming a config change and a group rename silently granting unrestricted access.

### Cache semantics

Keyed on `auth_user_id`, TTL 900s, storing `ErpIdentity` and `AccessLevel`. Removing someone from `Management` takes effect within 15 minutes. Invalidated on logout. No manual invalidation API — that is state to operate, and the TTL is short enough without it.

## Error handling

Every failure denies. No path degrades to wider access.

| Condition | Behaviour |
|---|---|
| ERP unreachable at login | Reject, 503. No fallback to an expired cache entry. |
| ERP unreachable on TTL refresh | Reject the request, 503. An expired cache is exactly when a revocation is most likely pending. |
| `auth_user` missing or `is_active = false` | Reject login. |
| No `person` row, or `person.is_active = false` | Reject login, "account not provisioned". |
| Configured group IDs missing or renamed | Boot failure, not request failure. |
| Unclassified table in a `SELF` query | `ValueError` from `sql_guard`, surfaced to the agent as a tool observation (unchanged). |
| Slack user with no `hr_assistant_users` row | Unchanged: not-registered response. |

This makes ERP availability a hard dependency of login, where today an ERP outage only degrades queries. Accepted deliberately: the alternative is serving stale authorization.

## Testing

`tests/test_rbac.py` currently holds 105 tests; roughly 60 assert on `Role`, `scope_prompt()` text, or dept/team injection and will be deleted or rewritten. They test behaviour that ceases to exist.

New coverage:

- **`AccessLevel.resolve`** — table-driven: in `Pod` → unrestricted; in `Management` → unrestricted; in both → unrestricted; in neither → self; in `Advanced POD Permissions` but not `Pod` → **self** (the name-collision case).
- **Self predicate injection** — one test per table class, mirroring the existing `dept_head` cases: `person`, each `_PERSON_FK_TABLES` shape, `_PERSON_TEAM_FK_TABLES`, person-free (asserting *no* injection), unclassified (asserting raise).
- **`OR 1=1` defeat under self scope** — port `test_dept_head_scope_defeats_or_injection` to the new predicate. The highest-value test in the suite.
- **Identity resolution** against a fixture ERP schema — email→person happy path, missing person, inactive person, inactive `auth_user`, group membership via `auth_user_groups`.
- **Cache** — hit inside TTL, refetch after expiry, no serve-stale-on-ERP-error.
- **Boot assertions** — group ID missing raises; group name mismatch raises.
- **End-to-end** in `tests/test_scope_execution.py` — a `SELF` user asking to list all employees receives only their own row.

Per `CLAUDE.md`, test execution and test authoring are delegated to a Sonnet or Haiku agent rather than run in this session.

## Migration

Each step is independently deployable.

1. Add `ErpIdentityResolver`, `AccessLevel`, config keys, and boot assertions. Nothing consumes them yet. Ship.
2. Rewrite `ScopePolicy` and the `sql_guard` predicates behind the two-level model. `RBACContext.for_user` accepts both `HRUser` and `ErpIdentity` during the transition.
3. Switch the HTTP and Slack call sites to resolve via ERP. `HRUser.role` is then read by nothing.
4. Alembic migration drops `role`, `department_id`, `team_id` from `hr_assistant_users`. The values are not reconstructible — snapshot the table before running.
5. Delete `roles.py`, replace `prompt.py` with `scope_hint()`, prune dead tests.

### Pre-cutover verification gate

Against production, before step 3:

- Confirm the configured group IDs resolve to `Pod` and `Management`.
- Inspect their membership for plausibility — `Pod` should look like People Ops, not 30 engineers.

If production membership looks like the dev copy's, stop. That would mean the group choice is wrong, not the design.

## Consequences

**New exposure.** `SELF` users can read all 12 person-free lookup tables company-wide: every department name, every team name, every leave type, the full holiday calendar. Via the documented `team.lead_id` exception in `_APPROVED_PERSON_FK_EXCEPTIONS`, a normal employee can also enumerate who leads every team. This matches the existing classification's intent, but under the current design nobody below `dept_head` could reach the bot at all, so it is new in practice.

**Population change.** Access shifts from a handful of hand-registered rows to every employee with an ERP account, most of them at `SELF` scope.

## Out of scope

Stated so it is not assumed:

- Salary, CNIC, and DOB stay in `FORBIDDEN_COLUMNS` for **everyone**, including Pod and Management. `person_compensation_report` is not honoured. Unchanged from today.
- No per-table permission gating. Access is all of `INCLUDED_TABLES` or a self-scoped subset of it. `auth_permission` codenames are not read at all in this design — only group membership is.
- Team leads receive no widened scope. A lead who is in neither group sees only their own data.
- `is_superuser` is not honoured as an access grant. A superuser must be in `Pod` or `Management` like anyone else.
