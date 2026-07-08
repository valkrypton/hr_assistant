"""
Unit tests for core.agent._build_agent's db.run wrapper (`_scoped_run`).

The wrapper passes every SQL statement through sql_guard.rewrite_sql before
execution. Previously a ValueError raised by rewrite_sql (forbidden column,
wildcard, non-SELECT, unclassified table, ...) would propagate and abort the
whole agent run. It must instead be caught and turned into an
`f"Error: {exc}"` string so LangChain surfaces it as a tool observation the
agent can see and retry against — LangChain's run_no_throw only catches
SQLAlchemyError, not ValueError.

Building the full LangChain SQL agent needs a real BaseLanguageModel (a
MagicMock/FakeListChatModel fails pydantic validation or lacks bind_tools),
so these tests stub out only `create_sql_agent` — the last step of
_build_agent — to return the already-wrapped `db` object directly. Every
other line of _build_agent (SQLDatabase construction, wrapping db.run) runs
for real, against a throwaway sqlite file. No network and no real LLM call
is involved.
"""

import sqlite3
from unittest.mock import patch

import pytest


@pytest.fixture()
def sqlite_db_url(tmp_path):
    """A throwaway sqlite file with a minimal `person` table."""
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE person (id INTEGER PRIMARY KEY, full_name TEXT, department_id INTEGER)"
    )
    conn.commit()
    conn.close()
    return f"sqlite:///{db_path}"


def _build_scoped_db(sqlite_db_url):
    """
    Build the real db object with the real `_scoped_run` wrapper attached,
    by stubbing out create_sql_agent (the only piece that needs a real LLM).
    Runs every other line of _build_agent for real, including the
    `from core.rbac.sql_guard import rewrite_sql as _rewrite` import that the
    wrapper closes over — so a test that wants to force rewrite_sql to raise
    must patch core.rbac.sql_guard.rewrite_sql BEFORE calling this, since
    `_rewrite` is a closure-local bound at _build_agent call time, not a
    patchable core.agent module attribute.
    """
    import core.agent as agent_mod
    from core.config import settings

    def _fake_create_sql_agent(llm, db, **kwargs):
        return db

    with (
        patch.object(settings, "INCLUDED_TABLES", ["person"]),
        patch.object(settings, "DATABASE_URL", sqlite_db_url),
        patch("core.agent.get_llm", return_value=object()),
        patch("core.agent.create_sql_agent", side_effect=_fake_create_sql_agent),
    ):
        return agent_mod._build_agent(None)


@pytest.fixture()
def scoped_db(sqlite_db_url):
    return _build_scoped_db(sqlite_db_url)


class TestScopedRunErrorHandling:
    def test_non_select_returns_error_string_instead_of_raising(self, scoped_db):
        result = scoped_db.run("DELETE FROM person WHERE id = 1")
        assert isinstance(result, str)
        assert result.startswith("Error:")
        assert "Non-SELECT" in result

    def test_valid_select_executes_normally_not_an_error(self, scoped_db):
        result = scoped_db.run("SELECT id FROM person")
        assert not result.startswith("Error:")

    def test_wrapper_returns_error_for_any_rewrite_sql_valueerror(self, sqlite_db_url):
        # Contract test: whatever rewrite_sql raises, the wrapper must turn it
        # into "Error: <message>" rather than letting it propagate. Must patch
        # sql_guard.rewrite_sql BEFORE _build_agent runs — see _build_scoped_db.
        with patch(
            "core.rbac.sql_guard.rewrite_sql",
            side_effect=ValueError("boom: guard tripped"),
        ):
            db = _build_scoped_db(sqlite_db_url)
            result = db.run("SELECT id FROM person")
        assert result == "Error: boom: guard tripped"
