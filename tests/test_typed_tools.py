"""
Tests for the typed tools (HRASSISTAN — PR8 first typed tools).

Covers:
  - team_roster: unscoped vs. dept-scoped rosters, team_name filtering, and
    exclusion of ended (past) team assignments
  - leave_lookup: approved-leave overlap matching, exclusion of pending leave
    and out-of-range leave, and dept scoping
  - joiners_summary: group_by='none'/'classification'/'cohort_attrition',
    the unknown-group_by error path, and dept scoping
  - permission inclusion: the three tools are exposed to a role that holds
    their required permission (denial mechanics live in test_tool_registry.py)

These tools build their own SQL and run it through core.tools.sql.run_via_guard,
so scope injection and forbidden-column blocking come from the guard — not
reimplemented here. Seed layout follows tests/test_tools_sql.py: a throwaway
file-based SQLite DB pointed at by settings.DATABASE_URL / erp_engine().
"""

import ast
import sqlite3

import pytest

from core.config import settings
from core.db import erp_engine
from core.identity.context import AgentContext
from core.rbac.context import RBACContext
from core.rbac.roles import Role
from core.tools import JOINERS_SUMMARY, LEAVE_LOOKUP, TEAM_ROSTER, registry

# ---------------------------------------------------------------------------
# Seed
# ---------------------------------------------------------------------------
#
#     Departments: 3 (Engineering), 4 (Sales)
#     Teams:       7 (Alpha), 8 (Beta)
#     Employment types: 1 = Employee (employed), 2 = Contract (subcontractor)
#
#     Person  Dept  Employment    Joined       Current team
#     101 Alice  3   employed     2024-03-01   Alpha (7)
#     102 Bob    3   subcontract  2024-06-01   Beta (8)
#     103 Carol  4   employed     2023-05-01   Alpha (7)
#     104 Dave   4   employed     2024-01-10   Beta (8)
#
#     Alice also has an ENDED team-8 assignment (must not appear in a roster).
#     Dave resigns (approved) with last_working_day 2024-11-01 — a same-year
#     (2024) joiner+leaver, i.e. counted by cohort_attrition.
#
#     Leave (query range used throughout: 2024-05-01..2024-05-15):
#       Alice (101, dept 3): approved, 2024-05-05..2024-05-07 -> overlaps, IN
#       Bob   (102, dept 3): PENDING,  2024-05-06..2024-05-08 -> excluded
#       Carol (103, dept 4): approved, 2024-01-01..2024-01-05 -> outside, excluded
#       Dave  (104, dept 4): approved, 2024-05-10..2024-05-20 -> overlaps, IN

_SCHEMA = """
CREATE TABLE department (id INTEGER PRIMARY KEY, name TEXT);
CREATE TABLE team (id INTEGER PRIMARY KEY, name TEXT);
CREATE TABLE employment_type (id INTEGER PRIMARY KEY, type INTEGER, name TEXT);
CREATE TABLE person (
    id INTEGER PRIMARY KEY, full_name TEXT, department_id INTEGER,
    employment_type_id INTEGER, joining_date TEXT, is_active INTEGER
);
CREATE TABLE person_team (
    id INTEGER PRIMARY KEY, person_id INTEGER, nsubteam_id INTEGER,
    end_date TEXT, is_active INTEGER, billable INTEGER
);
CREATE TABLE leave_record (
    id INTEGER PRIMARY KEY, person_id INTEGER, status INTEGER,
    start TEXT, "end" TEXT
);
CREATE TABLE users_personresignation (
    id INTEGER PRIMARY KEY, person_id INTEGER, status INTEGER, last_working_day TEXT
);
"""

_SEED = [
    "INSERT INTO department VALUES (3, 'Engineering'), (4, 'Sales')",
    "INSERT INTO team VALUES (7, 'Alpha'), (8, 'Beta')",
    "INSERT INTO employment_type VALUES (1, 1, 'Employee'), (2, 2, 'Contract')",
    """INSERT INTO person
        (id, full_name, department_id, employment_type_id, joining_date, is_active)
       VALUES
        (101, 'Alice', 3, 1, '2024-03-01', 1),
        (102, 'Bob',   3, 2, '2024-06-01', 1),
        (103, 'Carol', 4, 1, '2023-05-01', 1),
        (104, 'Dave',  4, 1, '2024-01-10', 1)""",
    """INSERT INTO person_team
        (id, person_id, nsubteam_id, end_date, is_active, billable) VALUES
        (1, 101, 7, NULL, 1, 1),
        (2, 102, 8, NULL, 1, 0),
        (3, 103, 7, NULL, 1, 1),
        (4, 104, 8, NULL, 1, 1),
        (5, 101, 8, '2024-01-01', 0, 0)""",
    """INSERT INTO leave_record (id, person_id, status, start, "end") VALUES
        (1, 101, 1, '2024-05-05', '2024-05-07'),
        (2, 102, 0, '2024-05-06', '2024-05-08'),
        (3, 103, 1, '2024-01-01', '2024-01-05'),
        (4, 104, 1, '2024-05-10', '2024-05-20')""",
    """INSERT INTO users_personresignation (id, person_id, status, last_working_day) VALUES
        (1, 104, 1, '2024-11-01')""",
]


@pytest.fixture()
def erp_db(tmp_path):
    """Point settings.DATABASE_URL / erp_engine() at a seeded temp-FILE SQLite
    DB (the tools execute via erp_engine(), so a :memory: DB would not be
    visible to them)."""
    db_path = tmp_path / "erp.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(_SCHEMA)
    for stmt in _SEED:
        conn.execute(stmt)
    conn.commit()
    conn.close()

    orig = settings.DATABASE_URL
    settings.DATABASE_URL = f"sqlite:///{db_path}"
    erp_engine.cache_clear()
    try:
        yield
    finally:
        settings.DATABASE_URL = orig
        erp_engine.cache_clear()


def _rows(result: str) -> list[tuple]:
    return ast.literal_eval(result)


def _names(result: str) -> set:
    """First-column (full_name) values as a set, for team_roster/leave_lookup."""
    return {r[0] for r in _rows(result)}


def _count(result: str) -> int:
    """The single COUNT(*) value from a joiners_summary(group_by='none') result."""
    return _rows(result)[0][0]


def _dept_head_ctx(department_id: int) -> AgentContext:
    return AgentContext.for_rbac(RBACContext(role=Role.DEPT_HEAD, department_id=department_id))


# ---------------------------------------------------------------------------
# team_roster
# ---------------------------------------------------------------------------


class TestTeamRoster:
    def test_unscoped_returns_all_current_members(self, erp_db):
        result = TEAM_ROSTER.execute(None, team_name=None)
        assert _names(result) == {"Alice", "Bob", "Carol", "Dave"}

    def test_dept_head_sees_only_own_department(self, erp_db):
        result = TEAM_ROSTER.execute(_dept_head_ctx(3), team_name=None)
        assert _names(result) == {"Alice", "Bob"}

    def test_team_name_filter_narrows_to_named_team(self, erp_db):
        result = TEAM_ROSTER.execute(None, team_name="Alpha")
        assert _names(result) == {"Alice", "Carol"}

    def test_ended_assignment_excluded(self, erp_db):
        # Alice's ended team-8 (Beta) row must not put her in Beta's roster,
        # and must not duplicate her in the unscoped roster either.
        result = TEAM_ROSTER.execute(None, team_name="Beta")
        assert _names(result) == {"Bob", "Dave"}
        assert "Alice" not in _names(result)

        unscoped = TEAM_ROSTER.execute(None, team_name=None)
        assert len(_rows(unscoped)) == 4


# ---------------------------------------------------------------------------
# leave_lookup
# ---------------------------------------------------------------------------


class TestLeaveLookup:
    def test_returns_approved_leave_overlapping_range(self, erp_db):
        result = LEAVE_LOOKUP.execute(None, start_date="2024-05-01", end_date="2024-05-15")
        assert _names(result) == {"Alice", "Dave"}

    def test_excludes_pending_leave(self, erp_db):
        result = LEAVE_LOOKUP.execute(None, start_date="2024-05-01", end_date="2024-05-15")
        assert "Bob" not in _names(result)

    def test_excludes_leave_outside_range(self, erp_db):
        result = LEAVE_LOOKUP.execute(None, start_date="2024-05-01", end_date="2024-05-15")
        assert "Carol" not in _names(result)

    def test_dept_head_scoping_returns_only_own_dept(self, erp_db):
        result = LEAVE_LOOKUP.execute(
            _dept_head_ctx(3), start_date="2024-05-01", end_date="2024-05-15"
        )
        assert _names(result) == {"Alice"}


# ---------------------------------------------------------------------------
# joiners_summary
# ---------------------------------------------------------------------------


class TestJoinersSummary:
    def test_group_by_none_counts_joiners_in_year(self, erp_db):
        result = JOINERS_SUMMARY.execute(None, year=2024, group_by="none")
        assert _count(result) == 3  # Alice, Bob, Dave (Carol joined 2023)

    def test_group_by_classification_splits_employed_vs_subcontractor(self, erp_db):
        result = JOINERS_SUMMARY.execute(None, year=2024, group_by="classification")
        breakdown = dict(_rows(result))
        assert breakdown == {"employed": 2, "subcontractor": 1}

    def test_group_by_cohort_attrition_counts_same_year_joiners_and_leavers(self, erp_db):
        result = JOINERS_SUMMARY.execute(None, year=2024, group_by="cohort_attrition")
        assert _count(result) == 1  # only Dave joined and left in 2024

    def test_unknown_group_by_returns_error_string(self, erp_db):
        result = JOINERS_SUMMARY.execute(None, year=2024, group_by="bogus")
        assert result.startswith("Error:")

    def test_dept_head_scoping_reduces_count(self, erp_db):
        result = JOINERS_SUMMARY.execute(_dept_head_ctx(3), year=2024, group_by="none")
        assert _count(result) == 2  # Alice + Bob only, dept 3


# ---------------------------------------------------------------------------
# Permission inclusion
# ---------------------------------------------------------------------------


class TestPermissionInclusion:
    def test_team_lead_sees_the_three_typed_tools(self):
        ctx = AgentContext.for_rbac(RBACContext(role=Role.TEAM_LEAD, team_id=1))
        available = registry.for_context(ctx)

        assert TEAM_ROSTER in available
        assert LEAVE_LOOKUP in available
        assert JOINERS_SUMMARY in available
