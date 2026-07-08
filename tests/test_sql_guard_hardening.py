"""
Regression tests for the PR2 sql_guard audit findings (HRASSISTAN).

Covers:
  - _BLOCKED_FUNCTIONS denylist (query_to_xml, dblink, pg_read_file, pg_sleep,
    lo_import) is enforced for every role, including unrestricted (CTO/CEO).
  - Schema-qualified calls (pg_catalog.pg_read_file(...)) are caught too.
  - SELECT ... INTO is blocked (it writes a table).
  - Legitimate typed aggregates (COUNT, AVG) are NOT over-blocked.

No database or LLM involved — all pure unit tests, matching tests/test_rbac.py
style.
"""

import pytest

from core.rbac.context import RBACContext
from core.rbac.roles import Role
from core.rbac.sql_guard import rewrite_sql

# Representative sample of blocked functions named in the audit finding.
BLOCKED_FUNCTION_QUERIES = [
    "SELECT query_to_xml('SELECT 1', false, false, '') AS x",
    "SELECT dblink('dbname=x', 'SELECT 1') AS x",
    "SELECT pg_read_file('/etc/passwd') AS x",
    "SELECT pg_sleep(5) AS x",
    "SELECT lo_import('/etc/passwd') AS x",
]

CONTEXTS = {
    "superuser": RBACContext.superuser(),
    "dept_head": RBACContext(role=Role.DEPT_HEAD, department_id=3),
    "team_lead": RBACContext(role=Role.TEAM_LEAD, team_id=7),
}


class TestBlockedFunctionsAllRoles:
    @pytest.mark.parametrize("ctx_name", list(CONTEXTS.keys()))
    @pytest.mark.parametrize("sql", BLOCKED_FUNCTION_QUERIES)
    def test_blocked_function_raises(self, ctx_name, sql):
        ctx = CONTEXTS[ctx_name]
        with pytest.raises(ValueError, match="Function blocked by scope guard"):
            rewrite_sql(sql, ctx)

    @pytest.mark.parametrize("ctx_name", list(CONTEXTS.keys()))
    def test_schema_qualified_blocked_function_raises(self, ctx_name):
        ctx = CONTEXTS[ctx_name]
        sql = "SELECT pg_catalog.pg_read_file('/etc/passwd') AS x"
        with pytest.raises(ValueError, match="Function blocked by scope guard"):
            rewrite_sql(sql, ctx)


class TestSelectIntoAllRoles:
    @pytest.mark.parametrize("ctx_name", list(CONTEXTS.keys()))
    def test_select_into_raises(self, ctx_name):
        ctx = CONTEXTS[ctx_name]
        sql = "SELECT full_name INTO exfil FROM person"
        with pytest.raises(ValueError, match="SELECT ... INTO blocked by scope guard"):
            rewrite_sql(sql, ctx)


class TestTypedAggregatesNotOverBlocked:
    """The function denylist must not catch legitimate typed aggregates."""

    def test_count_star_allowed_for_superuser(self):
        result = rewrite_sql("SELECT COUNT(*) FROM person", CONTEXTS["superuser"])
        assert "COUNT" in result.upper()

    def test_avg_competency_score_allowed_for_superuser(self):
        result = rewrite_sql("SELECT AVG(competency_score) FROM person", CONTEXTS["superuser"])
        assert "AVG" in result.upper()
