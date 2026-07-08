"""
Tests for the guarded SQL tool (HRASSISTAN — PR5 tool registry + query_erp_sql).

Covers:
  - execute_guarded_sql() guard-rejection contract: forbidden column,
    non-SELECT, and blocked-function SQL all return "Error: ..." strings
    without raising
  - execute_guarded_sql() scoped vs. unscoped execution against a seeded ERP
    (same seed layout as tests/test_scope_execution.py, so results match that
    module's oracle)
  - SqlCollector telemetry: populated on success, untouched on guard rejection
  - QUERY_ERP_SQL.fn's ctx-scoped collector creation/reuse

No LLM involved. The ERP engine points at a throwaway file-based SQLite DB for
the duration of each test that needs one (execute_guarded_sql runs the
rewritten SQL as-is — unlike test_scope_execution.py it does not transpile to
the SQLite dialect — so only postgres/sqlite-compatible query shapes are used
here).
"""

import ast
import sqlite3

import pytest

from core.config import settings
from core.db import erp_engine
from core.identity.context import AgentContext
from core.rbac.context import RBACContext
from core.rbac.roles import Role
from core.tools import QUERY_ERP_SQL
from core.tools.sql import SqlCollector, execute_guarded_sql

# Seed layout mirrors tests/test_scope_execution.py exactly:
#     dept 3 (Engineering) = {101, 102}
#     dept 4 (Sales)       = {103, 104}
#     everyone             = {101, 102, 103, 104}
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
    """INSERT INTO person_team (id, person_id, nsubteam_id, end_date, is_active) VALUES
        (1, 101, 7, NULL, 1),
        (2, 102, 8, NULL, 1),
        (3, 103, 7, NULL, 1),
        (4, 104, 8, NULL, 1),
        (5, 101, 8, '2024-01-01', 0)""",
    """INSERT INTO leave_record (id, person_id, status) VALUES
        (1,101,1),(2,102,1),(3,103,1),(4,104,1)""",
    """INSERT INTO person_week_log (id, person_id, is_completed) VALUES
        (1,101,0),(2,102,0),(3,103,0),(4,104,0)""",
]

DEPT3_PERSONS = {101, 102}
ALL_PERSONS = {101, 102, 103, 104}


@pytest.fixture()
def erp_db(tmp_path):
    """Point settings.DATABASE_URL / erp_engine() at a seeded temp-FILE SQLite
    DB (execute_guarded_sql opens its own connection via erp_engine(), so a
    :memory: DB would not be visible to it)."""
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


def _ids(result: str) -> set:
    """Parse the "[(101,), (102,)]"-shaped observation string back into a set
    of first-column ints."""
    rows = ast.literal_eval(result)
    return {r[0] for r in rows}


# ---------------------------------------------------------------------------
# Guard-rejection contract
# ---------------------------------------------------------------------------


class TestGuardRejectionContract:
    def test_forbidden_column_returns_error_string(self):
        result = execute_guarded_sql("SELECT salary FROM person", None)
        assert isinstance(result, str)
        assert result.startswith("Error:")

    def test_non_select_returns_error_string(self):
        result = execute_guarded_sql("DELETE FROM person", None)
        assert isinstance(result, str)
        assert result.startswith("Error:")

    def test_blocked_function_returns_error_string(self):
        result = execute_guarded_sql(
            "SELECT query_to_xml('SELECT salary FROM person', true, false, '')", None
        )
        assert isinstance(result, str)
        assert result.startswith("Error:")

    def test_rejections_do_not_raise(self):
        # Sanity check that all three shapes above are handled without
        # exceptions escaping — already implied by the tests above returning
        # normally, but asserted explicitly per the guard's documented
        # "returns Error:, never raises" contract.
        for sql in (
            "SELECT salary FROM person",
            "DELETE FROM person",
            "SELECT query_to_xml('SELECT salary FROM person', true, false, '')",
        ):
            try:
                result = execute_guarded_sql(sql, None)
            except Exception as exc:  # pragma: no cover - failure path
                pytest.fail(f"execute_guarded_sql raised {exc!r} for {sql!r}")
            assert result.startswith("Error:")


# ---------------------------------------------------------------------------
# Scoped vs. unscoped execution against the seeded ERP
# ---------------------------------------------------------------------------


class TestScopedExecution:
    def test_dept_head_sees_only_own_department(self, erp_db):
        ctx = RBACContext(role=Role.DEPT_HEAD, department_id=3)
        result = execute_guarded_sql("SELECT id FROM person", ctx)
        assert _ids(result) == DEPT3_PERSONS

    def test_unscoped_ctx_sees_all_persons(self, erp_db):
        result = execute_guarded_sql("SELECT id FROM person", None)
        assert _ids(result) == ALL_PERSONS


# ---------------------------------------------------------------------------
# SqlCollector telemetry
# ---------------------------------------------------------------------------


class TestSqlCollector:
    def test_successful_call_records_statement_tables_and_row_count(self, erp_db):
        collector = SqlCollector()
        result = execute_guarded_sql("SELECT id FROM person", None, collector=collector)

        assert not result.startswith("Error:")
        assert len(collector.statements) == 1
        assert "person" in collector.tables
        assert collector.rows_returned == len(ALL_PERSONS)

    def test_guard_rejected_call_does_not_record(self, erp_db):
        collector = SqlCollector()
        result = execute_guarded_sql("DELETE FROM person", None, collector=collector)

        assert result.startswith("Error:")
        assert collector.statements == []
        assert collector.tables == set()
        assert collector.rows_returned == 0


# ---------------------------------------------------------------------------
# QUERY_ERP_SQL.fn — ctx-scoped collector creation
# ---------------------------------------------------------------------------


class TestToolTelemetryViaContext:
    def test_execute_creates_and_stores_collector_on_ctx_metadata(self, erp_db):
        ctx = AgentContext.for_rbac(RBACContext.superuser())
        result = QUERY_ERP_SQL.execute(ctx, sql="SELECT id FROM person")

        assert not result.startswith("Error:")
        collector = ctx.metadata["_sql_collector"]
        assert isinstance(collector, SqlCollector)
        assert len(collector.statements) == 1
