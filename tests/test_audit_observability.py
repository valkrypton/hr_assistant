"""
Tests for audit observability (PR9 — tools_used / sql_statements / model_name /
rows_returned) and append-only enforcement.

Covers:
  - observability_fields(): maps AgentRunResult -> write_audit kwargs, empty
    (legacy) results map to all-None, long sql_statements are truncated
  - End-to-end through core.execution.run_query(): the four columns are
    persisted for a populated result and NULL for an empty/legacy result
  - Append-only trigger: Postgres-only, skipped unless TEST_POSTGRES_URL is set

Mirrors tests/test_execution.py's app_db fixture (temp-file SQLite, patched
settings.APP_DATABASE_URL, app_engine's lru_cache cleared) since run_query()
opens its own core.db.db_session() sessions internally.
"""

import os
from unittest.mock import patch

import pytest
import sqlalchemy
from sqlalchemy.orm import Session

from core.config import settings
from core.db import app_engine
from core.execution import run_query
from core.rbac.models import AuditLog, Base, HRUser
from core.rbac.roles import Role
from core.runtimes.base import AgentRunResult
from core.telemetry.audit import observability_fields

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def app_db(tmp_path):
    """Point settings.APP_DATABASE_URL at a fresh temp-file SQLite DB and
    clear core.db.app_engine's lru_cache so run_query() picks it up."""
    test_db_url = f"sqlite:///{tmp_path}/audit_observability_test.db"
    orig = settings.APP_DATABASE_URL
    settings.APP_DATABASE_URL = test_db_url
    app_engine.cache_clear()
    try:
        engine = sqlalchemy.create_engine(test_db_url)
        Base.metadata.create_all(engine)
        yield engine
    finally:
        settings.APP_DATABASE_URL = orig
        app_engine.cache_clear()


@pytest.fixture()
def session(app_db):
    with Session(app_db) as s:
        yield s


def add_user(session, **kwargs):
    defaults = {
        "employee_id": 1,
        "role": Role.CTO_CEO.value,
        "slack_user_id": "U_DEFAULT",
        "is_active": True,
    }
    defaults.update(kwargs)
    user = HRUser(**defaults)
    session.add(user)
    session.commit()
    return user


class StubRuntime:
    """Returns a canned AgentRunResult, recording every request it's called
    with (mirrors StubRuntime in tests/test_execution.py)."""

    def __init__(self, result):
        self.calls = []
        self._result = result

    def run(self, request):
        self.calls.append(request)
        return self._result


def make_populated_result(answer="stub answer") -> AgentRunResult:
    return AgentRunResult(
        answer=answer,
        tables_accessed="person",
        schema_rag_ms=1,
        agent_ms=2,
        total_ms=3,
        prompt_tokens=10,
        completion_tokens=5,
        total_tokens=15,
        sql_statements=["SELECT id FROM person", "SELECT id FROM department"],
        tools_used=["query_erp_sql", "lookup_department"],
        model_name="gpt-4o",
        rows_returned=7,
    )


# ---------------------------------------------------------------------------
# observability_fields() — unit tests
# ---------------------------------------------------------------------------


class TestObservabilityFields:
    def test_populated_result_maps_to_expected_dict(self):
        result = AgentRunResult(
            answer="ok",
            tools_used=["query_erp_sql", "x"],
            sql_statements=["SELECT 1", "SELECT 2"],
            model_name="gpt-4o",
            rows_returned=5,
        )

        fields = observability_fields(result)

        assert fields == {
            "tools_used": "query_erp_sql, x",
            "sql_statements": "SELECT 1; SELECT 2",
            "model_name": "gpt-4o",
            "rows_returned": 5,
        }

    def test_empty_legacy_result_maps_all_fields_to_none(self):
        result = AgentRunResult(answer="ok")

        fields = observability_fields(result)

        assert fields == {
            "tools_used": None,
            "sql_statements": None,
            "model_name": None,
            "rows_returned": None,
        }

    def test_long_sql_statements_are_truncated_to_4000_chars(self):
        # Each statement is 100 chars; enough of them blow past the 4000 cap.
        statements = [f"SELECT {i:093d} FROM person" for i in range(100)]
        result = AgentRunResult(answer="ok", sql_statements=statements)

        fields = observability_fields(result)

        assert len(fields["sql_statements"]) <= 4000


# ---------------------------------------------------------------------------
# End-to-end through the pipeline
# ---------------------------------------------------------------------------


class TestObservabilityPersistedThroughPipeline:
    def test_populated_result_persists_observability_columns(self, session):
        add_user(session, slack_user_id="U_OBS", employee_id=42, role=Role.CTO_CEO.value)
        result = make_populated_result(answer="42 employees")
        stub = StubRuntime(result)

        with patch("core.execution.get_runtime", return_value=stub):
            run_query("U_OBS", "How many employees?")

        row = session.query(AuditLog).filter_by(slack_user_id="U_OBS").one()
        assert row.tools_used == "query_erp_sql, lookup_department"
        # sql_statements is stored exactly as the runtime returned it: this
        # SQL is pre-execution and already forbidden-column-filtered by the
        # SQL guard, so persisting it verbatim cannot leak forbidden VALUES.
        assert row.sql_statements == "SELECT id FROM person; SELECT id FROM department"
        assert row.model_name == "gpt-4o"
        assert row.rows_returned == 7

    def test_legacy_style_result_persists_null_observability_columns(self, session):
        add_user(session, slack_user_id="U_LEGACY", employee_id=43, role=Role.CTO_CEO.value)
        result = AgentRunResult(answer="legacy answer", tables_accessed="person")
        stub = StubRuntime(result)

        with patch("core.execution.get_runtime", return_value=stub):
            run_query("U_LEGACY", "How many employees?")

        row = session.query(AuditLog).filter_by(slack_user_id="U_LEGACY").one()
        assert row.tools_used is None
        assert row.sql_statements is None
        assert row.model_name is None
        assert row.rows_returned is None


# ---------------------------------------------------------------------------
# Append-only enforcement — Postgres-only (BEFORE UPDATE/DELETE trigger from
# migrations/versions/0002_audit_observability.py). Not reproducible on the
# SQLite test DB, so this is skipped unless a live Postgres URL is provided.
# ---------------------------------------------------------------------------

_PG_URL = os.environ.get("TEST_POSTGRES_URL")


@pytest.mark.skipif(
    not _PG_URL,
    reason=(
        "Append-only enforcement is implemented as a Postgres BEFORE UPDATE/DELETE "
        "trigger (migrations/versions/0002_audit_observability.py) and cannot be "
        "reproduced on the sqlite test DB. Set TEST_POSTGRES_URL to a live Postgres "
        "connection string to exercise this test."
    ),
)
class TestAppendOnlyEnforcement:
    def test_update_and_delete_are_rejected_by_the_trigger(self):
        import sqlalchemy as sa

        engine = sa.create_engine(_PG_URL)
        with engine.begin() as conn:
            Base.metadata.create_all(conn)
            conn.execute(
                sa.text("""
                CREATE OR REPLACE FUNCTION hr_assistant_audit_no_mutate() RETURNS trigger AS $$
                BEGIN
                  RAISE EXCEPTION 'hr_assistant_audit is append-only: % is not allowed', TG_OP;
                END;
                $$ LANGUAGE plpgsql;
                """)
            )
            conn.execute(
                sa.text("""
                DROP TRIGGER IF EXISTS hr_assistant_audit_append_only ON hr_assistant_audit;
                CREATE TRIGGER hr_assistant_audit_append_only
                  BEFORE UPDATE OR DELETE ON hr_assistant_audit
                  FOR EACH ROW EXECUTE FUNCTION hr_assistant_audit_no_mutate();
                """)
            )

        with Session(engine) as session:
            row = AuditLog(slack_user_id="U_PG", question="q", answer="a")
            session.add(row)
            session.commit()
            row_id = row.id

        with Session(engine) as session, pytest.raises(sa.exc.DBAPIError):
            session.execute(
                sa.text("UPDATE hr_assistant_audit SET answer='x' WHERE id=:id"),
                {"id": row_id},
            )
            session.commit()

        with Session(engine) as session, pytest.raises(sa.exc.DBAPIError):
            session.execute(sa.text("DELETE FROM hr_assistant_audit WHERE id=:id"), {"id": row_id})
            session.commit()
