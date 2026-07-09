"""
"Mis-listed table" guard test (docs/rbac-hardening-roadmap.md, Testing
evolution).

sql_guard's `_PERSON_FREE_TABLES` allowlist lets restricted roles (dept_head,
team_lead) read a table company-wide, unscoped. If a person-bearing table
were ever wrongly added to that set, a restricted role would read it
UNSCOPED — a silent cross-department/cross-team data leak.

What this test proves (the fail-closed contract):
  - A table that is in NEITHER `_PERSON_FK_TABLES`/`_PERSON_TEAM_FK_TABLES`
    NOR `_PERSON_FREE_TABLES` — i.e. genuinely unclassified — makes
    `rewrite_sql` raise ValueError for a restricted role, rather than
    silently returning it unscoped. This is the guard's default: "if in
    doubt, deny," which protects the common case of forgetting to classify
    a newly added table.
  - A genuinely person-free table (`department`) IS allowed company-wide for
    the same restricted role, documenting the intended allowlist behavior
    (contrast case).

Residual risk this test does NOT catch: a person-bearing table that is
wrongly ADDED to `_PERSON_FREE_TABLES` (i.e. mis-classified, not
unclassified) would still be read unscoped — the fail-closed default only
protects tables nobody classified at all. Catching a wrong classification
requires human review of any change to `_PERSON_FREE_TABLES` (or the
Hypothesis-based property test the roadmap proposes next), not this guard.
"""

import pytest

from core.rbac.context import RBACContext
from core.rbac.roles import Role
from core.rbac.sql_guard import rewrite_sql


def _dept_head_ctx() -> RBACContext:
    return RBACContext(role=Role.DEPT_HEAD, department_id=3)


class TestUnclassifiedTableFailsClosed:
    def test_unclassified_table_raises_for_restricted_role(self):
        ctx = _dept_head_ctx()
        # "some_unregistered_table" is deliberately absent from every
        # sql_guard classification set (person, FK, team-FK, person-free).
        with pytest.raises(ValueError, match="not classified for scope enforcement"):
            rewrite_sql("SELECT id FROM some_unregistered_table", ctx)

    def test_person_free_table_is_allowed_company_wide(self):
        # Contrast case: "department" holds no per-person data and is
        # deliberately listed in _PERSON_FREE_TABLES, so it's readable
        # company-wide even for a restricted role — no raise.
        ctx = _dept_head_ctx()
        result = rewrite_sql("SELECT id, name FROM department", ctx)
        assert "department" in result.lower()
