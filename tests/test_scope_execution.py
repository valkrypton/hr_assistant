"""
Execution-based RBAC scope tests.

Unlike tests/test_sql_guard_self_scope.py (which asserts on the *text* of the
rewritten SQL), these tests seed a real database, run rewrite_sql, EXECUTE
the result, and assert on the ROWS returned. This is the layer that catches
scope bypasses which produce syntactically-plausible SQL that still leaks
data — e.g. the CROSS JOIN bypass, where a scope predicate appears in the
query but is anchored to the wrong table.

The guard emits Postgres-dialect SQL. By default it's transpiled to SQLite
for hermetic, infra-free execution — the injected scope predicates (equality
/ IN-subquery) behave identically in both engines, so SQLite faithfully tests
the row-scoping property, and Postgres-only *attack* SQL (to_jsonb(p), etc.)
is rejected by the guard before execution, so it never depends on the DB
dialect either way. Set TEST_DATABASE_URL to run this same suite against a
real Postgres instance instead (no transpile) — see the postgres: service
container in .github/workflows/ci.yml.

Seed layout:

    Departments: 3 (Engineering), 4 (Sales)
    Teams:       7 (Alpha),       8 (Beta)

    Person  Dept  Active team membership
    101 Alice   3   team 7        (+ an INACTIVE, ended membership on team 8)
    102 Bob     3   team 8
    103 Carol   4   team 7
    104 Dave    4   team 8

    => everyone = {101, 102, 103, 104}

Self-scope tests anchor on Alice (101). The dept/team divergence in the seed
predates the two-level access model — it originally guarded against a
dept<->team cross-wiring bug that no longer applies, but is kept because it
still exercises the CROSS JOIN anchor bug: the guard must scope leave_record
to Alice's own person_id regardless of what person is cross-joined in.
"""

import os
import sqlite3

import pytest
import sqlglot

from core.rbac.access import AccessLevel
from core.rbac.context import RBACContext
from core.rbac.sql_guard import rewrite_sql

# When set, run this whole suite against real Postgres instead of transpiled
# SQLite — closes the "Dialect gap" testing finding (Postgres-only parse/emit
# edge cases like lateral joins, DISTINCT ON, window functions are never
# exercised against SQLite). CI wires this up via a postgres: service
# container (.github/workflows/ci.yml); unset locally, the suite runs against
# SQLite exactly as before.
_TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")

ALICE = 101
ALICE_ONLY = {101}
ALL_PERSONS = {101, 102, 103, 104}


_SCHEMA = """
CREATE TABLE department (id INTEGER PRIMARY KEY, name TEXT);
CREATE TABLE team (id INTEGER PRIMARY KEY, name TEXT);
CREATE TABLE person (
    id INTEGER PRIMARY KEY, full_name TEXT, department_id INTEGER,
    status_id INTEGER, is_active BOOLEAN, joining_date TEXT, separation_date TEXT
);
CREATE TABLE person_team (
    id INTEGER PRIMARY KEY, person_id INTEGER, nsubteam_id INTEGER,
    end_date TEXT, is_active BOOLEAN
);
CREATE TABLE leave_record (id INTEGER PRIMARY KEY, person_id INTEGER, status INTEGER);
CREATE TABLE person_week_log (id INTEGER PRIMARY KEY, person_id INTEGER, is_completed INTEGER);
"""

_SEED = [
    "INSERT INTO department VALUES (3, 'Engineering'), (4, 'Sales')",
    "INSERT INTO team VALUES (7, 'Alpha'), (8, 'Beta')",
    """INSERT INTO person (id, full_name, department_id, status_id, is_active) VALUES
        (101, 'Alice', 3, 10, TRUE),
        (102, 'Bob',   3, 10, TRUE),
        (103, 'Carol', 4, 10, TRUE),
        (104, 'Dave',  4, 10, TRUE)""",
    # nsubteam_id, end_date (NULL = active), is_active — types match the real
    # ERP schema (core/context/schema.md): is_active is boolean, not integer.
    # SQLite accepts TRUE/FALSE as literals too (3.23+), so this seed works
    # unchanged on both backends.
    """INSERT INTO person_team (id, person_id, nsubteam_id, end_date, is_active) VALUES
        (1, 101, 7, NULL, TRUE),
        (2, 102, 8, NULL, TRUE),
        (3, 103, 7, NULL, TRUE),
        (4, 104, 8, NULL, TRUE),
        (5, 101, 8, '2024-01-01', FALSE)""",  # Alice's OLD, inactive team-8 row — must not count
    "INSERT INTO leave_record (id, person_id, status) VALUES (1,101,1),(2,102,1),(3,103,1),(4,104,1)",
    "INSERT INTO person_week_log (id, person_id, is_completed) VALUES (1,101,0),(2,102,0),(3,103,0),(4,104,0)",
]


_TABLES_NEWEST_FIRST = (
    "person_week_log",
    "leave_record",
    "person_team",
    "person",
    "team",
    "department",
)


class _SqliteBackend:
    """In-memory, hermetic, infra-free — the default. rewrite_sql's Postgres
    output is transpiled to SQLite before execution."""

    dialect = "sqlite"

    def __init__(self):
        self._conn = sqlite3.connect(":memory:")
        self._conn.executescript(_SCHEMA)
        for stmt in _SEED:
            self._conn.execute(stmt)
        self._conn.commit()

    def execute(self, guarded_sql: str) -> list[tuple]:
        sqlite_sql = sqlglot.transpile(guarded_sql, read="postgres", write="sqlite")[0]
        return self._conn.execute(sqlite_sql).fetchall()

    def close(self):
        self._conn.close()


class _PostgresBackend:
    """Real Postgres — runs the guard's native output directly, no
    transpile. Schema is created fresh and dropped per test for isolation,
    same as SQLite's per-test :memory: connection."""

    dialect = "postgres"

    def __init__(self, url: str):
        import psycopg2

        self._conn = psycopg2.connect(url)
        self._conn.autocommit = True
        with self._conn.cursor() as cur:
            cur.execute(f"DROP TABLE IF EXISTS {', '.join(_TABLES_NEWEST_FIRST)} CASCADE")
            cur.execute(_SCHEMA)
            for stmt in _SEED:
                cur.execute(stmt)

    def execute(self, guarded_sql: str) -> list[tuple]:
        with self._conn.cursor() as cur:
            cur.execute(guarded_sql)
            return cur.fetchall()

    def close(self):
        with self._conn.cursor() as cur:
            cur.execute(f"DROP TABLE IF EXISTS {', '.join(_TABLES_NEWEST_FIRST)} CASCADE")
        self._conn.close()


@pytest.fixture()
def conn():
    backend = _PostgresBackend(_TEST_DATABASE_URL) if _TEST_DATABASE_URL else _SqliteBackend()
    yield backend
    backend.close()


def _run(conn, sql: str, ctx) -> list[tuple]:
    """Guard the SQL, execute against whichever backend `conn` wraps."""
    guarded = rewrite_sql(sql, ctx)
    return conn.execute(guarded)


def _ids(rows) -> set:
    """Collect the first column of each row as a set of ints."""
    return {r[0] for r in rows}


def _ctx(person_id):
    """Self-scoped context for the given person."""
    return RBACContext(access_level=AccessLevel.SELF, person_id=person_id)


# ---------------------------------------------------------------------------
# Self scope — sees only their own row, whatever the query shape
# ---------------------------------------------------------------------------


class TestSelfScopeExecution:
    def test_direct_person_select(self, conn):
        rows = _run(conn, "SELECT id, department_id FROM person", _ctx(ALICE))
        assert _ids(rows) == ALICE_ONLY

    def test_cross_join_person_does_not_leak_fk_table(self, conn):
        # The CROSS JOIN bypass: person appears but is not linked to leave_record.
        # Pre-fix this returned every person's leave rows.
        rows = _run(
            conn,
            "SELECT lr.person_id FROM leave_record lr CROSS JOIN person p",
            _ctx(ALICE),
        )
        assert _ids(rows) == ALICE_ONLY

    def test_fk_table_alone_is_scoped(self, conn):
        rows = _run(conn, "SELECT person_id FROM leave_record", _ctx(ALICE))
        assert _ids(rows) == ALICE_ONLY

    def test_person_team_fk_table_is_scoped(self, conn):
        rows = _run(conn, "SELECT person_id FROM person_week_log", _ctx(ALICE))
        assert _ids(rows) == ALICE_ONLY

    def test_or_injection_cannot_widen(self, conn):
        rows = _run(
            conn,
            "SELECT id, department_id FROM person WHERE id = 102 OR 1=1",
            _ctx(ALICE),
        )
        assert _ids(rows) == ALICE_ONLY

    def test_subquery_on_person_is_scoped(self, conn):
        rows = _run(
            conn,
            "SELECT lr.id FROM leave_record lr WHERE lr.person_id IN (SELECT id FROM person)",
            _ctx(ALICE),
        )
        # leave_record row belonging to Alice (101) is id 1.
        assert _ids(rows) == {1}

    def test_missing_person_id_returns_nothing(self, conn):
        """A SELF context with no person_id is misconfigured — deny, don't widen."""
        broken = RBACContext(access_level=AccessLevel.SELF, person_id=None)
        rows = _run(conn, "SELECT id FROM person", broken)
        assert rows == []


# ---------------------------------------------------------------------------
# Unrestricted — full access, and NOT over-restricted
# ---------------------------------------------------------------------------


class TestUnrestrictedExecution:
    def test_unrestricted_sees_all_people(self, conn):
        rows = _run(conn, "SELECT id, department_id FROM person", RBACContext.unrestricted())
        assert _ids(rows) == ALL_PERSONS

    def test_none_ctx_sees_all(self, conn):
        rows = _run(conn, "SELECT person_id FROM leave_record", None)
        assert _ids(rows) == ALL_PERSONS
