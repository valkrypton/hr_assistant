# RBAC table-classification gaps

Open items surfaced while adding `sql_guard.assert_tables_classified` /
`assert_person_free_tables_have_no_person_fk` (startup checks that every
`INCLUDED_TABLES` entry is classified as person-linked or person-free, and
that no person-free table secretly carries a person FK). Recorded here
rather than guessed at, since getting a classification wrong is exactly the
silent-leak failure mode these checks exist to prevent.

## `team.lead_id` — resolved: approved exception, not scoped

**Status:** resolved. `team` stays in `_PERSON_FREE_TABLES`;
`("team", "lead_id")` is listed in `sql_guard._APPROVED_PERSON_FK_EXCEPTIONS`
so the mis-listed-table check doesn't flag it.

**Shape:** `team.lead_id` is a direct FK to `person.id`
(`scripts/seed_erp.py`, `core/context/schema.md`) — flagged by an
independent security review as soon as `assert_person_free_tables_have_no_person_fk`
was added, since against the real ERP schema it raised `RuntimeError` at
startup (the app couldn't boot). Unlike `peer_review` below, `team` is
core to most canonical queries (SPEC.md's "who's on the backend team", etc.)
so excluding it from `INCLUDED_TABLES` the way `peer_review` was excluded
would have been a severe functional regression, not a safe default.

**The decision:** which person leads a team is treated as org-chart
metadata — the same category as department names, which are also visible
company-wide regardless of the requester's department/team scope. It is
not on the forbidden-columns list (salary/CNIC/DOB/bank details/personal
addresses/phone numbers/medical records — FR-5.8) and is not "whose record
is this" employee data the way, say, a leave record or a competency score
is. Confirmed as a product decision rather than a default silently applied.

**If this is ever revisited:** the fix would be a dedicated scoping helper
(not a reuse of `_fk_scope_sql`, which hardcodes the FK column name to
`person_id` — `team`'s FK column is `lead_id`) that either redacts
`lead_id` for out-of-scope teams or restricts which `team` rows are
returned entirely. Either changes today's behavior (company-wide visible)
and would need the same kind of sign-off this decision got.

## `peer_review` — needs a product decision, not just a technical one

**Status:** excluded from `INCLUDED_TABLES` (see `.env`) until this is
resolved. Not visible to the agent at all right now.

**Shape:** `peer_review` has two person-linked FKs, each two hops from
`person`:

```
peer_review.review_for_id -> reward_presentation_people.id -> reward_presentation_people.person_id -> person.id   (reviewee)
peer_review.review_by_id  -> reward_presentation_people.id -> reward_presentation_people.person_id -> person.id   (reviewer)
```

The existing indirect-link scoping in `core/rbac/sql_guard.py`
(`_person_team_fk_scope_sql`) only handles a single column named
`person_team_id` pointing at `person_team.id` — it doesn't generalize to
two independent two-hop FKs on the same table.

**The open question:** for a restricted role (dept_head / team_lead), which
`peer_review` rows should be visible?

- **OR** — either the reviewee or the reviewer is in scope. More
  permissive: e.g. a team lead sees a review where their report reviewed
  someone on another team, or vice versa.
- **AND** — both reviewee and reviewer must be in scope. Stricter, matches
  the codebase's existing fail-closed bias, but may hide reviews the
  requester arguably should see (e.g. their report being reviewed by an
  external reviewer).

This needs sign-off from whoever owns the RBAC scope definitions before
either the classification or the scoping SQL is written — picking one
silently would be encoding a business rule about who can see peer-review
data without anyone having actually decided it.

**Once decided:**
1. Classify `reward_presentation_people` into `_PERSON_FK_TABLES` (it has a
   direct `person_id` FK) if it's ever queried directly, or leave it
   unclassified-but-referenced-only-in-scoping-subqueries if it's never
   selected from directly — same pattern `person` itself already gets away
   with by being referenced in `_fk_scope_sql`'s subqueries.
2. Add a new scoping helper in `sql_guard.py` for the two-hop, two-column
   shape (not a reuse of `_person_team_fk_scope_sql`, which is hardcoded to
   `person_team_id` / `person_team`).
3. Add `peer_review` back to `INCLUDED_TABLES` and to whichever
   classification set is created for it.
4. Add execution-based scope tests (`tests/test_scope_execution.py` pattern)
   proving the chosen OR/AND semantics actually hold — seed data where the
   reviewer and reviewee are in different departments/teams so the two
   semantics would produce different results, the same way the existing
   suite deliberately diverges department/team membership to catch
   cross-wiring.
