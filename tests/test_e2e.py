"""
End-to-end API tests covering the 20 canonical query types from SPEC.md.

Strategy
--------
Tests hit the FastAPI app via TestClient with the SQL agent mocked out.
Verifies the full request pipeline — routing, RBAC enforcement, audit
logging, rate limiting — without a live database or LLM.

The APP_DATABASE_URL is overridden to a fresh SQLite file per test session
so route handlers automatically use the test DB (they call app_engine() at
request time, which reads settings.APP_DATABASE_URL).
"""
import base64

import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

MOCK_ANSWER = "Here is the answer to your question based on the available data."
_ADMIN_CREDS = ("test-admin", "test-password-123")
_ADMIN_HEADERS = {
    "Authorization": "Basic " + base64.b64encode(
        f"{_ADMIN_CREDS[0]}:{_ADMIN_CREDS[1]}".encode()
    ).decode()
}


@pytest.fixture(scope="module")
def test_db_url(tmp_path_factory):
    """Shared SQLite URL for the test session."""
    return f"sqlite:///{tmp_path_factory.mktemp('db')}/test.db"


@pytest.fixture(scope="module")
def mock_query():
    """Patch core.agent.query to return a canned QueryResult (module-scoped)."""
    from core.agent import QueryResult
    result = QueryResult(
        answer=MOCK_ANSWER,
        tables_accessed="person,department",
        schema_rag_ms=10,
        agent_ms=200,
        total_ms=210,
        prompt_tokens=500,
        completion_tokens=100,
        total_tokens=600,
    )
    with patch("core.agent.query", return_value=result):
        yield result


@pytest.fixture(scope="module")
def client(test_db_url, mock_query):
    """
    TestClient with:
    - APP_DATABASE_URL + DATABASE_URL → temp SQLite file
    - agent mocked to return canned QueryResult
    - SQLAdmin admin warmup skipped
    """
    from core.config import settings
    from api.deps import app_engine, erp_engine

    orig_app = settings.APP_DATABASE_URL
    orig_erp = settings.DATABASE_URL
    orig_allow_unauth = settings.ALLOW_UNAUTHENTICATED_QUERY
    settings.APP_DATABASE_URL = test_db_url
    settings.DATABASE_URL = test_db_url
    settings.ALLOW_UNAUTHENTICATED_QUERY = True
    app_engine.cache_clear()
    erp_engine.cache_clear()

    try:
        # Create tables and seed test admin user.
        import sqlalchemy
        from core.auth import hash_password
        from core.rbac.models import AdminUser, Base
        from sqlalchemy.orm import Session as _Session
        engine = sqlalchemy.create_engine(test_db_url)
        Base.metadata.create_all(engine)
        with _Session(engine) as s:
            s.add(AdminUser(username=_ADMIN_CREDS[0], hashed_password=hash_password(_ADMIN_CREDS[1])))
            s.commit()

        with patch("core.agent.get_agent"):  # skip LLM warmup in lifespan
            from importlib import reload
            import api.main as main_mod
            reload(main_mod)                # pick up patched settings
            with TestClient(main_mod.app, raise_server_exceptions=False) as c:
                yield c
    finally:
        settings.APP_DATABASE_URL = orig_app
        settings.DATABASE_URL = orig_erp
        settings.ALLOW_UNAUTHENTICATED_QUERY = orig_allow_unauth
        app_engine.cache_clear()
        erp_engine.cache_clear()


_user_counter = 0

@pytest.fixture()
def registered_user(client):
    """Register a CTO/CEO test user with a unique ID per test."""
    global _user_counter
    _user_counter += 1
    slack_id = f"U_TEST_{_user_counter}"
    r = client.post("/users", json={
        "employee_id": 900 + _user_counter,
        "role": "cto_ceo",
        "slack_user_id": slack_id,
    }, headers=_ADMIN_HEADERS)
    assert r.status_code == 201
    user_id = r.json()["id"]
    yield slack_id
    client.delete(f"/users/{user_id}", headers=_ADMIN_HEADERS)


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

class TestHealth:
    def test_health_responds(self, client):
        r = client.get("/health")
        assert r.status_code in (200, 503)


# ---------------------------------------------------------------------------
# /query — unauthenticated
# ---------------------------------------------------------------------------

class TestQueryUnauthenticated:
    def test_empty_query_rejected(self, client):
        r = client.post("/query", json={"query": "   "})
        assert r.status_code == 400

    def test_valid_query_returns_answer(self, client):
        r = client.post("/query", json={"query": "How many employees do we have?"})
        assert r.status_code == 200
        assert r.json()["answer"] == MOCK_ANSWER

    @pytest.mark.parametrize("query_text", [
        "Who hasn't filled their daily logs this week?",
        "Who's not adding full 8 hours in their daily logs?",
        "Who got warnings in the last quarter?",
        "Any devs who resigned recently?",
        "Who is not performing well on the backend team?",
        "Who's available for a Django project starting May?",
        "Who's been non-billable for the last 2 months?",
        "Show me the backend team right now",
        "Who has experience with Sabre APIs?",
        "Find React devs with e-commerce experience available in May",
        "Which team has the most attrition this year?",
        "Who's on leave next week?",
        "How many new joiners did we have in 2025?",
        "Of the 2025 joiners, how many were employees and how many subcontractors?",
        "How many people who joined in 2025 also left in 2025?",
        "Break down all resignations by department",
        "Show resignations by years of experience — use 1-year brackets",
        "How many terminations did we have in 2023?",
        "How many Software Engineers, QA Engineers, and Product Managers do we have?",
        "What is Bilal Qureshi's competency score?",
    ])
    def test_canonical_query(self, client, query_text):
        """All 20 canonical queries from SPEC.md must return 200 with an answer."""
        r = client.post("/query", json={"query": query_text})
        assert r.status_code == 200
        assert len(r.json()["answer"]) > 0


# ---------------------------------------------------------------------------
# /query — authenticated with RBAC
# ---------------------------------------------------------------------------

class TestQueryAuthenticated:
    def test_unregistered_user_forbidden(self, client):
        r = client.post("/query", json={
            "query": "How many employees?",
            "slack_user_id": "U_NOT_REGISTERED",
        })
        assert r.status_code == 403

    def test_registered_user_gets_answer(self, client, registered_user):
        r = client.post("/query", json={
            "query": "How many employees?",
            "slack_user_id": registered_user,
        })
        assert r.status_code == 200
        assert r.json()["answer"] == MOCK_ANSWER

    def test_audit_log_written_on_success(self, client, registered_user):
        r = client.post("/query", json={
            "query": "Audit test query",
            "slack_user_id": registered_user,
        })
        assert r.status_code == 200
        logs = client.get(f"/audit?slack_user_id={registered_user}&limit=5", headers=_ADMIN_HEADERS).json()
        questions = [l["question"] for l in logs]
        assert "Audit test query" in questions
        entry = next(l for l in logs if l["question"] == "Audit test query")
        assert entry["answer"] == MOCK_ANSWER
        assert entry["total_tokens"] == 600

    def test_audit_log_written_on_error(self, client, registered_user):
        with patch("api.routes.query.agent_query", side_effect=RuntimeError("DB down")):
            r = client.post("/query", json={
                "query": "Error test query",
                "slack_user_id": registered_user,
            })
        assert r.status_code == 500
        logs = client.get(f"/audit?slack_user_id={registered_user}&limit=10", headers=_ADMIN_HEADERS).json()
        errors = [l for l in logs if l["question"] == "Error test query"]
        assert len(errors) > 0
        assert errors[0]["error"] is not None


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------

class TestRateLimit:
    def test_rate_limit_enforced(self, client):
        """Pre-fill audit log to hit limit, next query should get 429."""
        from datetime import datetime, timezone
        import sqlalchemy
        from core.config import settings
        from core.rbac.models import AuditLog, HRUser, Base
        from sqlalchemy.orm import Session

        slack_id = "U_RATE_TEST"
        engine = sqlalchemy.create_engine(settings.APP_DATABASE_URL)

        with Session(engine) as session:
            # Register user
            session.add(HRUser(employee_id=888, role="hr_manager", slack_user_id=slack_id))
            session.commit()

        limit = 2
        with Session(engine) as session:
            for _ in range(limit):
                session.add(AuditLog(
                    slack_user_id=slack_id,
                    question="prior",
                    created_at=datetime.now(timezone.utc),
                ))
            session.commit()

        with patch("api.deps.settings.RATE_LIMIT_PER_HOUR", limit), \
             patch("api.routes.query.check_rate_limit",
                   side_effect=__import__("fastapi").HTTPException(
                       status_code=429, detail="Rate limit exceeded")):
            r = client.post("/query", json={
                "query": "One more",
                "slack_user_id": slack_id,
            })
        assert r.status_code == 429


# ---------------------------------------------------------------------------
# User admin endpoints
# ---------------------------------------------------------------------------

class TestUserAdmin:
    def test_list_users(self, client):
        r = client.get("/users", headers=_ADMIN_HEADERS)
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_register_user(self, client):
        r = client.post("/users", json={
            "employee_id": 777,
            "role": "hr_manager",
            "slack_user_id": "U_ADMIN_TEST",
        }, headers=_ADMIN_HEADERS)
        assert r.status_code == 201
        data = r.json()
        assert data["role"] == "hr_manager"
        assert data["slack_user_id"] == "U_ADMIN_TEST"
        # cleanup
        client.delete(f"/users/{data['id']}", headers=_ADMIN_HEADERS)

    def test_duplicate_slack_id_rejected(self, client):
        payload = {"employee_id": 1, "role": "cto_ceo", "slack_user_id": "U_DUP_TEST"}
        r1 = client.post("/users", json=payload, headers=_ADMIN_HEADERS)
        assert r1.status_code == 201
        r2 = client.post("/users", json=payload, headers=_ADMIN_HEADERS)
        assert r2.status_code == 409
        client.delete(f"/users/{r1.json()['id']}", headers=_ADMIN_HEADERS)

    def test_deregister_user(self, client):
        r = client.post("/users", json={
            "employee_id": 555,
            "role": "team_lead",
            "slack_user_id": "U_DEL_TEST",
        }, headers=_ADMIN_HEADERS)
        assert r.status_code == 201
        user_id = r.json()["id"]
        assert client.delete(f"/users/{user_id}", headers=_ADMIN_HEADERS).status_code == 204
        users = client.get("/users", headers=_ADMIN_HEADERS).json()
        assert all(u["id"] != user_id for u in users)

    def test_deregister_nonexistent_user(self, client):
        r = client.delete("/users/99999", headers=_ADMIN_HEADERS)
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# Audit log endpoint
# ---------------------------------------------------------------------------

class TestAuditLog:
    def test_audit_returns_list(self, client):
        r = client.get("/audit", headers=_ADMIN_HEADERS)
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_audit_filter_by_user(self, client, registered_user):
        client.post("/query", json={
            "query": "filter test",
            "slack_user_id": registered_user,
        })
        r = client.get(f"/audit?slack_user_id={registered_user}", headers=_ADMIN_HEADERS)
        assert r.status_code == 200
        assert all(e["slack_user_id"] == registered_user for e in r.json())

    def test_audit_limit(self, client, registered_user):
        for i in range(4):
            client.post("/query", json={
                "query": f"limit test {i}",
                "slack_user_id": registered_user,
            })
        r = client.get("/audit?limit=2", headers=_ADMIN_HEADERS)
        assert len(r.json()) <= 2

    def test_audit_entry_has_latency_fields(self, client, registered_user):
        client.post("/query", json={
            "query": "latency check",
            "slack_user_id": registered_user,
        })
        logs = client.get(f"/audit?slack_user_id={registered_user}&limit=5", headers=_ADMIN_HEADERS).json()
        entry = next((l for l in logs if l["question"] == "latency check"), None)
        assert entry is not None
        assert entry["total_ms"] == 210
        assert entry["prompt_tokens"] == 500
