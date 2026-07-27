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
        patch.object(settings, "INCLUDED_TABLES", "person"),
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


class TestErpDbCaching:
    """
    Regression tests for the engine/reflection caching fix: get_agent() used
    to call SQLDatabase.from_uri() (create_engine + full MetaData.reflect())
    on every restricted-role build, since only the unrestricted agent was
    cached — see core/agent.py:_erp_db. Each test uses its own throwaway
    sqlite file (sqlite_db_url), so the lru_cache key never collides across
    tests despite being process-wide.
    """

    def test_build_agent_reuses_cached_erp_db_across_calls(self, sqlite_db_url):
        import core.agent as agent_mod
        from core.agent.factory import SQLDatabase
        from core.config import settings

        def _fake_create_sql_agent(llm, db, **kwargs):
            return db

        with (
            patch.object(settings, "INCLUDED_TABLES", "person"),
            patch.object(settings, "DATABASE_URL", sqlite_db_url),
            patch("core.agent.get_llm", return_value=object()),
            patch("core.agent.create_sql_agent", side_effect=_fake_create_sql_agent),
            patch.object(SQLDatabase, "from_uri", wraps=SQLDatabase.from_uri) as from_uri_spy,
        ):
            # Simulates the shared unrestricted build plus two restricted
            # (dept_head / team_lead) builds against the same ERP target.
            agent_mod._build_agent(None)
            agent_mod._build_agent(None)
            agent_mod._build_agent(None)

        assert from_uri_spy.call_count == 1

    def test_scoped_builds_get_independent_run_wrappers(self, sqlite_db_url):
        """The cached SQLDatabase is shallow-copied per build (copy.copy), so
        each build's db.run monkeypatch is independent and doesn't leak onto
        the shared cached instance or onto other builds."""
        import core.agent as agent_mod
        from core.config import settings

        def _fake_create_sql_agent(llm, db, **kwargs):
            return db

        with (
            patch.object(settings, "INCLUDED_TABLES", "person"),
            patch.object(settings, "DATABASE_URL", sqlite_db_url),
            patch("core.agent.get_llm", return_value=object()),
            patch("core.agent.create_sql_agent", side_effect=_fake_create_sql_agent),
        ):
            db_a = agent_mod._build_agent(None)
            db_b = agent_mod._build_agent(None)

        assert db_a is not db_b
        assert db_a.run is not db_b.run

    def test_erp_db_engine_has_pool_pre_ping_and_recycle(self, sqlite_db_url):
        import core.agent as agent_mod

        db = agent_mod._erp_db(sqlite_db_url, ("person",))

        assert db._engine.pool._pre_ping is True
        assert db._engine.pool._recycle == 300


class TestTableClassificationGuards:
    """
    Regression tests for the two startup checks added alongside
    docs/rbac-classification-gaps.md: an *unlisted* INCLUDED_TABLES entry
    (assert_tables_classified) and a person-bearing table *mis*-listed as
    person-free (assert_person_free_tables_have_no_person_fk) must both fail
    closed at build time, not leak silently at query time.
    """

    def test_unclassified_included_table_raises_at_build_time(self, tmp_path):
        import sqlite3

        import core.agent as agent_mod
        from core.config import settings

        db_path = tmp_path / "unclassified.db"
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE person (id INTEGER PRIMARY KEY)")
        # `mystery` is in INCLUDED_TABLES but not in any of sql_guard's three
        # classification sets — this is exactly the gap the real .env hit
        # with available_time/peer_review before they were resolved.
        conn.execute("CREATE TABLE mystery (id INTEGER PRIMARY KEY)")
        conn.commit()
        conn.close()
        db_url = f"sqlite:///{db_path}"

        with (
            patch.object(settings, "INCLUDED_TABLES", "person,mystery"),
            patch.object(settings, "DATABASE_URL", db_url),
            patch("core.agent.get_llm", return_value=object()),
            patch("core.agent.create_sql_agent", side_effect=lambda llm, db, **kw: db),
        ):
            with pytest.raises(RuntimeError, match="mystery"):
                agent_mod._build_agent(None)

    def test_person_free_table_with_person_fk_raises_at_build_time(self, tmp_path):
        """The concrete 'mis-listed table' regression: a table wrongly placed
        in _PERSON_FREE_TABLES that actually carries a person_id column must
        be caught, not silently trusted."""
        import sqlite3

        import core.agent as agent_mod
        import core.rbac.sql_guard as sql_guard
        from core.config import settings

        db_path = tmp_path / "misclassified.db"
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE person (id INTEGER PRIMARY KEY)")
        conn.execute("CREATE TABLE leaky (id INTEGER PRIMARY KEY, person_id INTEGER, note TEXT)")
        conn.commit()
        conn.close()
        db_url = f"sqlite:///{db_path}"

        with (
            patch.object(settings, "INCLUDED_TABLES", "person,leaky"),
            patch.object(settings, "DATABASE_URL", db_url),
            patch("core.agent.get_llm", return_value=object()),
            patch("core.agent.create_sql_agent", side_effect=lambda llm, db, **kw: db),
            patch.object(
                sql_guard,
                "_PERSON_FREE_TABLES",
                frozenset(sql_guard._PERSON_FREE_TABLES | {"leaky"}),
            ),
            patch.object(
                sql_guard,
                "_ALL_CLASSIFIED_TABLES",
                sql_guard._ALL_CLASSIFIED_TABLES | {"leaky"},
            ),
        ):
            with pytest.raises(RuntimeError, match="leaky"):
                agent_mod._build_agent(None)

    def test_person_free_table_without_person_fk_is_fine(self, tmp_path):
        """Sanity check: a genuinely person-free table in INCLUDED_TABLES
        must NOT trip the mis-listed-table guard."""
        import sqlite3

        import core.agent as agent_mod
        from core.config import settings

        db_path = tmp_path / "clean.db"
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE person (id INTEGER PRIMARY KEY)")
        conn.execute("CREATE TABLE department (id INTEGER PRIMARY KEY, name TEXT)")
        conn.commit()
        conn.close()
        db_url = f"sqlite:///{db_path}"

        with (
            patch.object(settings, "INCLUDED_TABLES", "person,department"),
            patch.object(settings, "DATABASE_URL", db_url),
            patch("core.agent.get_llm", return_value=object()),
            patch("core.agent.create_sql_agent", side_effect=lambda llm, db, **kw: db),
        ):
            # department is already in _PERSON_FREE_TABLES — must not raise.
            agent_mod._build_agent(None)

    def test_declared_fk_under_a_non_person_id_name_is_still_caught(self, tmp_path):
        """The name heuristic (col.name == "person_id") only catches one
        shape of mis-listing. A declared FOREIGN KEY constraint into person
        under a different column name (e.g. team.lead_id in the real ERP
        schema — see docs/rbac-classification-gaps.md) must be caught too,
        via the has_person_fk branch, not just the name check."""
        import sqlite3

        import core.agent as agent_mod
        import core.rbac.sql_guard as sql_guard
        from core.config import settings

        db_path = tmp_path / "declared_fk.db"
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE person (id INTEGER PRIMARY KEY)")
        conn.execute(
            "CREATE TABLE owned_thing ("
            "id INTEGER PRIMARY KEY, owner_id INTEGER, "
            "FOREIGN KEY(owner_id) REFERENCES person(id))"
        )
        conn.commit()
        conn.close()
        db_url = f"sqlite:///{db_path}"

        with (
            patch.object(settings, "INCLUDED_TABLES", "person,owned_thing"),
            patch.object(settings, "DATABASE_URL", db_url),
            patch("core.agent.get_llm", return_value=object()),
            patch("core.agent.create_sql_agent", side_effect=lambda llm, db, **kw: db),
            patch.object(
                sql_guard,
                "_PERSON_FREE_TABLES",
                frozenset(sql_guard._PERSON_FREE_TABLES | {"owned_thing"}),
            ),
            patch.object(
                sql_guard,
                "_ALL_CLASSIFIED_TABLES",
                sql_guard._ALL_CLASSIFIED_TABLES | {"owned_thing"},
            ),
        ):
            with pytest.raises(RuntimeError, match="owned_thing"):
                agent_mod._build_agent(None)

    def test_team_lead_id_approved_exception_does_not_raise(self, tmp_path):
        """team.lead_id is a real FK into person (confirmed against
        scripts/seed_erp.py / core/context/schema.md) but is an explicit,
        documented product decision (docs/rbac-classification-gaps.md) not
        to scope it — must not trip the guard."""
        import sqlite3

        import core.agent as agent_mod
        from core.config import settings

        db_path = tmp_path / "team_lead_id.db"
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE person (id INTEGER PRIMARY KEY)")
        conn.execute(
            "CREATE TABLE team ("
            "id INTEGER PRIMARY KEY, name TEXT, lead_id INTEGER, "
            "FOREIGN KEY(lead_id) REFERENCES person(id))"
        )
        conn.commit()
        conn.close()
        db_url = f"sqlite:///{db_path}"

        with (
            patch.object(settings, "INCLUDED_TABLES", "person,team"),
            patch.object(settings, "DATABASE_URL", db_url),
            patch("core.agent.get_llm", return_value=object()),
            patch("core.agent.create_sql_agent", side_effect=lambda llm, db, **kw: db),
        ):
            # team is already in _PERSON_FREE_TABLES and lead_id is already
            # in _APPROVED_PERSON_FK_EXCEPTIONS — must not raise.
            agent_mod._build_agent(None)
