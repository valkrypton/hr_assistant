"""
Regression tests for the dangerous-function / SELECT-INTO scope-guard fix.

Previously a restricted role could exfiltrate any column of any table
(salary, NIC, DOB, ...) via query_to_xml('SELECT ... FROM person') or
similar functions whose SQL-string argument the guard never inspected.
rewrite_sql() must now reject these for every role, including unrestricted
ones, before the SQL ever reaches the database.
"""

import pytest

from core.rbac.context import RBACContext
from core.rbac.roles import Role
from core.rbac.sql_guard import rewrite_sql

CONTEXTS = {
    "superuser": RBACContext.superuser(),
    "dept_head": RBACContext(role=Role.DEPT_HEAD, department_id=3),
    "team_lead": RBACContext(role=Role.TEAM_LEAD, team_id=7),
}

DANGEROUS_FUNCTION_CALLS = [
    "SELECT query_to_xml('SELECT salary FROM person', true, false, '')",
    "SELECT dblink('dbname=x', 'SELECT 1')",
    "SELECT pg_read_file('/etc/passwd')",
    "SELECT pg_sleep(10)",
    "SELECT lo_import('/etc/passwd')",
]

NEWLY_BLOCKED_FUNCTION_CALLS = [
    "SELECT lo_get(16385)",
    "SELECT pg_ls_tmpdir('base/pgsql_tmp')",
    "SELECT dblink_get_result('c')",
    "SELECT dblink_connect('x','y')",
]


class TestDangerousFunctionsBlockedForAllRoles:
    @pytest.mark.parametrize("ctx_name", list(CONTEXTS))
    @pytest.mark.parametrize("sql", DANGEROUS_FUNCTION_CALLS)
    def test_dangerous_function_call_raises(self, ctx_name, sql):
        ctx = CONTEXTS[ctx_name]
        with pytest.raises(ValueError, match="Function blocked by scope guard"):
            rewrite_sql(sql, ctx)

    @pytest.mark.parametrize("sql", NEWLY_BLOCKED_FUNCTION_CALLS)
    def test_newly_blocked_function_call_raises_for_superuser(self, sql):
        with pytest.raises(ValueError, match="Function blocked by scope guard"):
            rewrite_sql(sql, CONTEXTS["superuser"])

    @pytest.mark.parametrize("ctx_name", list(CONTEXTS))
    def test_schema_qualified_call_raises(self, ctx_name):
        ctx = CONTEXTS[ctx_name]
        with pytest.raises(ValueError, match="Function blocked by scope guard"):
            rewrite_sql("SELECT pg_catalog.pg_read_file('/etc/passwd')", ctx)

    @pytest.mark.parametrize("ctx_name", list(CONTEXTS))
    def test_select_into_raises(self, ctx_name):
        ctx = CONTEXTS[ctx_name]
        with pytest.raises(ValueError, match="SELECT ... INTO blocked by scope guard"):
            rewrite_sql("SELECT full_name INTO exfil FROM person", ctx)


class TestLegitimateSuperuserQueriesUnaffected:
    """The guard must not over-block typed aggregate functions or plain SELECTs."""

    def test_count_star_allowed(self):
        result = rewrite_sql("SELECT COUNT(*) FROM person", CONTEXTS["superuser"])
        assert "COUNT(*)" in result

    def test_avg_aggregate_allowed(self):
        result = rewrite_sql("SELECT AVG(competency_score) FROM person", CONTEXTS["superuser"])
        assert "AVG" in result

    def test_plain_select_allowed(self):
        result = rewrite_sql("SELECT full_name FROM person", CONTEXTS["superuser"])
        assert "full_name" in result
