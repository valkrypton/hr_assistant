"""
Tests for the query execution pipeline (HRASSISTAN — PR4 runtime interface +
execution pipeline).

core.execution.run_query() sequences: rate limit -> identity -> runtime ->
redaction -> audit. These tests patch the runtime seam (a stub AgentRuntime)
so the pipeline's own orchestration is exercised without a live LLM.

Uses a temp-file SQLite app DB (mirrors tests/test_e2e.py's client fixture) —
run_query() opens its own core.db.db_session() sessions internally, so a
file-backed DB (not :memory:) is required for the seeding session and the
pipeline's sessions to see the same committed rows.

Patch target note: core/execution.py does `from core.runtimes import
get_runtime`, which binds `get_runtime` as a name in core.execution's own
module namespace at import time. Patching `core.runtimes.get_runtime` does
NOT affect that already-bound reference — the pipeline must be patched at
`core.execution.get_runtime`.
"""

from datetime import UTC, datetime
from unittest.mock import patch

import pytest
import sqlalchemy
from sqlalchemy.orm import Session

from core.config import settings
from core.db import app_engine
from core.errors import RateLimitExceeded, UserNotRegistered
from core.execution import run_query
from core.rbac.models import AuditLog, Base, HRUser
from core.rbac.roles import Role
from core.runtimes.base import AgentRunResult

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def app_db(tmp_path):
    """Point settings.APP_DATABASE_URL at a fresh temp-file SQLite DB and
    clear core.db.app_engine's lru_cache so run_query() picks it up."""
    test_db_url = f"sqlite:///{tmp_path}/execution_test.db"
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


def seed_audit_rows(session, slack_user_id, count):
    for _ in range(count):
        session.add(
            AuditLog(slack_user_id=slack_user_id, question="prior", created_at=datetime.now(UTC))
        )
    session.commit()


def make_result(answer="stub answer") -> AgentRunResult:
    return AgentRunResult(
        answer=answer,
        tables_accessed="person",
        schema_rag_ms=1,
        agent_ms=2,
        total_ms=3,
        prompt_tokens=10,
        completion_tokens=5,
        total_tokens=15,
    )


class StubRuntime:
    """Records every request it's called with; returns a canned result or
    raises a canned exception."""

    def __init__(self, result=None, exc=None):
        self.calls = []
        self._result = result
        self._exc = exc

    def run(self, request):
        self.calls.append(request)
        if self._exc is not None:
            raise self._exc
        return self._result

    def warmup(self):
        pass


# ---------------------------------------------------------------------------
# Identity / rate-limit gate — the LLM must never see an unknown or
# throttled caller.
# ---------------------------------------------------------------------------


class TestIdentityAndRateLimitGate:
    def test_unregistered_user_raises_and_runtime_never_called(self, app_db):
        stub = StubRuntime(result=make_result())
        with (
            patch("core.execution.get_runtime", return_value=stub),
            pytest.raises(UserNotRegistered),
        ):
            run_query("U_GHOST", "How many employees?")
        assert stub.calls == []

    def test_rate_limited_user_raises_and_runtime_never_called(self, session):
        add_user(session, slack_user_id="U_RATE", employee_id=50)
        seed_audit_rows(session, "U_RATE", 2)

        stub = StubRuntime(result=make_result())
        with (
            patch.object(settings, "RATE_LIMIT_PER_HOUR", 2),
            patch("core.execution.get_runtime", return_value=stub),
            pytest.raises(RateLimitExceeded),
        ):
            run_query("U_RATE", "How many employees?")
        assert stub.calls == []


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestHappyPath:
    def test_registered_user_gets_stub_result_and_audit_row(self, session):
        add_user(session, slack_user_id="U_HAPPY", employee_id=42, role=Role.CTO_CEO.value)
        result = make_result(answer="42 employees")
        stub = StubRuntime(result=result)

        with patch("core.execution.get_runtime", return_value=stub):
            returned = run_query("U_HAPPY", "How many employees?")

        assert returned is result
        assert returned.answer == "42 employees"
        assert len(stub.calls) == 1

        row = session.query(AuditLog).filter_by(slack_user_id="U_HAPPY").one()
        assert row.answer == "42 employees"
        assert row.role == Role.CTO_CEO.value
        assert row.employee_id == 42
        assert row.error is None


# ---------------------------------------------------------------------------
# Runtime failure
# ---------------------------------------------------------------------------


class TestRuntimeFailure:
    def test_runtime_exception_is_reraised_and_audited(self, session):
        add_user(session, slack_user_id="U_ERR", employee_id=7)
        stub = StubRuntime(exc=RuntimeError("boom"))

        with (
            patch("core.execution.get_runtime", return_value=stub),
            pytest.raises(RuntimeError, match="boom"),
        ):
            run_query("U_ERR", "bad question")

        row = session.query(AuditLog).filter_by(slack_user_id="U_ERR").one()
        assert row.error == "boom"
        assert row.answer is None


# ---------------------------------------------------------------------------
# Redaction — scoped vs unscoped
# ---------------------------------------------------------------------------


class TestRedaction:
    def test_scoped_user_answer_is_redacted(self, session):
        add_user(
            session,
            slack_user_id="U_DEPT",
            employee_id=8,
            role=Role.DEPT_HEAD.value,
            department_id=3,
        )
        stub = StubRuntime(result=make_result(answer="salary: 5000"))

        with patch("core.execution.get_runtime", return_value=stub):
            result = run_query("U_DEPT", "what's my salary")

        assert "5000" not in result.answer
        assert "[SALARY REDACTED]" in result.answer

    def test_unscoped_answer_is_returned_raw_and_runtime_is_called(self, session):
        stub = StubRuntime(result=make_result(answer="salary: 5000"))

        with patch("core.execution.get_runtime", return_value=stub):
            result = run_query(None, "what's my salary")

        assert result.answer == "salary: 5000"
        assert len(stub.calls) == 1

        row = session.query(AuditLog).filter_by(question="what's my salary").one()
        assert row.slack_user_id is None
        assert row.role is None
        assert row.employee_id is None
        assert row.answer == "salary: 5000"
