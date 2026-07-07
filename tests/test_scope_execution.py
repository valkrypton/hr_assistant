"""
Execution-based RBAC scope tests.

Unlike test_rbac.py (which asserts on the *text* of the rewritten SQL), these
tests seed a real database, run each restricted role's query through
rewrite_sql, EXECUTE it, and assert on the ROWS returned. This is the layer
that catches scope bypasses which produce syntactically-plausible SQL that
still leaks data — e.g. the CROSS JOIN bypass, where "department_id = 3"
appears in the query but is anchored to the wrong table.

The guard emits Postgres-dialect SQL; we transpile it to SQLite for hermetic,
infra-free execution. The injected scope predicates (equality / IN-subquery)
behave identically in both engines, so SQLite faithfully tests the row-scoping
property. Postgres-only *attack* SQL (to_jsonb(p), etc.) is rejected by the
guard before execution, so it never depends on the DB dialect.

Seed layout (team scope and department scope intentionally DIVERGE so an
accidental dept<->team cross-wiring is caught):

    Departments: 3 (Engineering), 4 (Sales)
    Teams:       7 (Alpha),       8 (Beta)

    Person  Dept  Active team membership
    101 Alice   3   team 7        (+ an INACTIVE, ended membership on team 8)
    102 Bob     3   team 8
    103 Carol   4   team 7
    104 Dave    4   team 8

    => dept 3   = {101, 102}
       team 7   = {101, 103}   (crosses departments; excludes Alice's dead team-8 row)
       team 8   = {102, 104}
       everyone = {101, 102, 103, 104}
"""
import sqlite3

import pytest
import sqlglot

from core.rbac.context import RBACContext
from core.rbac.roles import Role
from core.rbac.sql_guard import rewrite_sql


DEPT3_PERSONS = {101, 102}
TEAM7_PERSONS = {101, 103}
TEAM8_PERSONS = {102, 104}
ALL_PERSONS = {101, 102, 103, 104}


_SCHEMA = """
CREATE TABLE department (id INTEGER PRIMARY KEY, name TEXT);
CREATE TABLE team (id INTEGER PRIMARY KEY, name TEXT);
CREATE TABLE person (
    id INTEGER PRIMARY KEY, full_name TEXT, department_id INTEGER,
    status_id INTEGER, is_active INTEGER, joining_date TEXT, separation_date TEXT
);
CREATE TABLE person_team (
    id INTEGER PRIMARY KEY, person_id INTEGER, nsubteam_id INTEGER,
    end_date TEXT, is_active INTEGER
);
CREATE TABLE leave_record (id INTEGER PRIMARY KEY, person_id INTEGER, status INTEGER);
CREATE TABLE person_week_log (id INTEGER PRIMARY KEY, person_id INTEGER, is_completed INTEGER);
"""

_SEED = [
    "INSERT INTO department VALUES (3, 'Engineering'), (4, 'Sales')",
    "INSERT INTO team VALUES (7, 'Alpha'), (8, 'Beta')",
    """INSERT INTO person (id, full_name, department_id, status_id, is_active) VALUES
        (101, 'Alice', 3, 10, 1),
        (102, 'Bob',   3, 10, 1),
        (103, 'Carol', 4, 10, 1),
        (104, 'Dave',  4, 10, 1)""",
    # nsubteam_id, end_date (NULL = active), is_active
    """INSERT INTO person_team (id, person_id, nsubteam_id, end_date, is_active) VALUES
        (1, 101, 7, NULL, 1),
        (2, 102, 8, NULL, 1),
        (3, 103, 7, NULL, 1),
        (4, 104, 8, NULL, 1),
        (5, 101, 8, '2024-01-01', 0)""",   # Alice's OLD, inactive team-8 row — must not count
    "INSERT INTO leave_record (id, person_id, status) VALUES (1,101,1),(2,102,1),(3,103,1),(4,104,1)",
    "INSERT INTO person_week_log (id, person_id, is_completed) VALUES (1,101,0),(2,102,0),(3,103,0),(4,104,0)",
]


@pytest.fixture()
def conn():
    c = sqlite3.connect(":memory:")
    c.executescript(_SCHEMA)
    for stmt in _SEED:
        c.execute(stmt)
    c.commit()
    yield c
    c.close()


def _run(conn, sql: str, ctx) -> list[tuple]:
    """Guard the SQL, transpile Postgres->SQLite, execute, return rows."""
    guarded = rewrite_sql(sql, ctx)
    sqlite_sql = sqlglot.transpile(guarded, read="postgres", write="sqlite")[0]
    return conn.execute(sqlite_sql).fetchall()


def _ids(rows) -> set:
    """Collect the first column of each row as a set of ints."""
    return {r[0] for r in rows}


def _ctx(role, dept=None, team=None):
    return RBACContext(role=role, department_id=dept, team_id=team)


# ---------------------------------------------------------------------------
# Department head — sees only their own department, whatever the query shape
# ---------------------------------------------------------------------------

class TestDeptHeadExecution:
    def test_direct_person_select(self, conn):
        rows = _run(conn, "SELECT id, department_id FROM person", _ctx(Role.DEPT_HEAD, dept=3))
        assert _ids(rows) == DEPT3_PERSONS
        assert all(dept == 3 for _, dept in rows)

    def test_cross_join_person_does_not_leak_fk_table(self, conn):
        # The CROSS JOIN bypass: person appears but is not linked to leave_record.
        # Pre-fix this returned every department's leave rows.
        rows = _run(
            conn,
            "SELECT lr.person_id FROM leave_record lr CROSS JOIN person p",
            _ctx(Role.DEPT_HEAD, dept=3),
        )
        assert _ids(rows) <= DEPT3_PERSONS
        assert 103 not in _ids(rows) and 104 not in _ids(rows)

    def test_fk_table_alone_is_scoped(self, conn):
        rows = _run(conn, "SELECT person_id FROM leave_record", _ctx(Role.DEPT_HEAD, dept=3))
        assert _ids(rows) == DEPT3_PERSONS

    def test_or_injection_cannot_widen(self, conn):
        rows = _run(
            conn,
            "SELECT id, department_id FROM person WHERE department_id = 4 OR 1=1",
            _ctx(Role.DEPT_HEAD, dept=3),
        )
        assert _ids(rows) == DEPT3_PERSONS

    def test_subquery_on_person_is_scoped(self, conn):
        rows = _run(
            conn,
            "SELECT lr.id FROM leave_record lr WHERE lr.person_id IN (SELECT id FROM person)",
            _ctx(Role.DEPT_HEAD, dept=3),
        )
        # leave_record ids belong to dept-3 persons (101->1, 102->2)
        assert _ids(rows) == {1, 2}


# ---------------------------------------------------------------------------
# Team lead — sees only active members of their own team
# ---------------------------------------------------------------------------

class TestTeamLeadExecution:
    def test_direct_person_select(self, conn):
        rows = _run(conn, "SELECT id FROM person", _ctx(Role.TEAM_LEAD, team=7))
        assert _ids(rows) == TEAM7_PERSONS

    def test_fk_table_is_scoped(self, conn):
        rows = _run(conn, "SELECT person_id FROM person_week_log", _ctx(Role.TEAM_LEAD, team=7))
        assert _ids(rows) == TEAM7_PERSONS

    def test_inactive_membership_excluded(self, conn):
        # team 8 active members are {102, 104}; Alice's ended team-8 row must NOT
        # pull 101 into team-8 scope.
        rows = _run(conn, "SELECT id FROM person", _ctx(Role.TEAM_LEAD, team=8))
        assert _ids(rows) == TEAM8_PERSONS
        assert 101 not in _ids(rows)

    def test_cross_join_person_does_not_leak(self, conn):
        rows = _run(
            conn,
            "SELECT lr.person_id FROM leave_record lr CROSS JOIN person p",
            _ctx(Role.TEAM_LEAD, team=7),
        )
        assert _ids(rows) <= TEAM7_PERSONS


# ---------------------------------------------------------------------------
# Unrestricted roles — full access, and NOT over-restricted
# ---------------------------------------------------------------------------

class TestUnrestrictedExecution:
    @pytest.mark.parametrize("role", [Role.CTO_CEO, Role.HR_MANAGER])
    def test_sees_all_people(self, conn, role):
        rows = _run(conn, "SELECT id, department_id FROM person", _ctx(role))
        assert _ids(rows) == ALL_PERSONS

    def test_none_ctx_sees_all(self, conn):
        rows = _run(conn, "SELECT person_id FROM leave_record", None)
        assert _ids(rows) == ALL_PERSONS


# ---------------------------------------------------------------------------
# Misconfigured restricted roles — deny all (1 = 0), never leak
# ---------------------------------------------------------------------------

class TestMisconfiguredDeniesAll:
    def test_dept_head_no_dept_returns_nothing(self, conn):
        rows = _run(conn, "SELECT id FROM person", _ctx(Role.DEPT_HEAD, dept=None))
        assert rows == []

    def test_team_lead_no_team_returns_nothing(self, conn):
        rows = _run(conn, "SELECT person_id FROM leave_record", _ctx(Role.TEAM_LEAD, team=None))
        assert rows == []
