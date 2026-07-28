"""
End-to-end API tests covering the 20 canonical query types from SPEC.md.

Strategy
--------
Tests hit the FastAPI app via TestClient with the SQL agent mocked out.
Verifies the full request pipeline — routing and RBAC enforcement —
without a live database or LLM.

The APP_DATABASE_URL is overridden to a fresh SQLite file per test session
so route handlers automatically use the test DB (they call app_engine() at
request time, which reads settings.APP_DATABASE_URL).
"""

import base64
import json
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

MOCK_ANSWER = "Here is the answer to your question based on the available data."
_ADMIN_CREDS = ("test-admin", "test-password-123")
_ADMIN_HEADERS = {
    "Authorization": "Basic "
    + base64.b64encode(f"{_ADMIN_CREDS[0]}:{_ADMIN_CREDS[1]}".encode()).decode()
}


def _sse_events(response) -> list[tuple[str, dict]]:
    """Parse a text/event-stream /query response body into
    [(event_name, json_data), ...], skipping heartbeat comment lines."""
    events: list[tuple[str, dict]] = []
    for block in response.text.split("\n\n"):
        if not block.strip():
            continue
        event_name = None
        data_line = None
        for line in block.splitlines():
            if line.startswith("event:"):
                event_name = line[len("event:") :].strip()
            elif line.startswith("data:"):
                data_line = line[len("data:") :].strip()
        if event_name and data_line is not None:
            events.append((event_name, json.loads(data_line)))
    return events


def _sse_result(response) -> dict:
    """The `answer` or `error` event's data dict from an SSE /query
    response — the one event carrying the actual outcome, after any
    `status`/heartbeat events."""
    for name, data in _sse_events(response):
        if name in ("answer", "error"):
            return data
    raise AssertionError(f"No answer/error event in SSE body: {response.text!r}")


@pytest.fixture(scope="module")
def test_db_url(tmp_path_factory):
    """Shared SQLite URL for the test session."""
    return f"sqlite:///{tmp_path_factory.mktemp('db')}/test.db"


@pytest.fixture(scope="module")
def mock_query():
    """Patch core.agent.query to return a canned AgentQueryResult (module-scoped)."""
    from core.agent import AgentQueryResult

    result = AgentQueryResult(
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
    from api.deps import app_engine, erp_engine
    from core.config import settings

    orig_app = settings.APP_DATABASE_URL
    orig_erp = settings.DATABASE_URL
    settings.APP_DATABASE_URL = test_db_url
    settings.DATABASE_URL = test_db_url
    app_engine.cache_clear()
    erp_engine.cache_clear()

    try:
        # Create tables and seed test admin user.
        import sqlalchemy
        from sqlalchemy.orm import Session as _Session

        from api.auth import hash_password
        from core.rbac.models import AdminUser, Base

        engine = sqlalchemy.create_engine(test_db_url)
        Base.metadata.create_all(engine)
        with _Session(engine) as s:
            s.add(
                AdminUser(username=_ADMIN_CREDS[0], hashed_password=hash_password(_ADMIN_CREDS[1]))
            )
            s.commit()

        # DATABASE_URL == APP_DATABASE_URL here (same test SQLite file), so
        # resolve_context's ERP read needs these tables to exist even though
        # this file otherwise mocks the agent, not RBAC resolution. Minimal
        # stand-in for the ERP's auth_user/person/auth_user_groups — see
        # core/rbac/erp_identity.py for the queries these back.
        with engine.begin() as conn:
            conn.execute(
                sqlalchemy.text(
                    "CREATE TABLE IF NOT EXISTS auth_user (id INTEGER PRIMARY KEY, "
                    "is_active BOOLEAN NOT NULL, email VARCHAR(254) NOT NULL DEFAULT '')"
                )
            )
            conn.execute(
                sqlalchemy.text(
                    "CREATE TABLE IF NOT EXISTS person (id INTEGER PRIMARY KEY, "
                    "user_id INTEGER NOT NULL, is_active BOOLEAN NOT NULL)"
                )
            )
            conn.execute(
                sqlalchemy.text(
                    "CREATE TABLE IF NOT EXISTS auth_user_groups (id INTEGER PRIMARY KEY, "
                    "user_id INTEGER NOT NULL, group_id INTEGER NOT NULL)"
                )
            )

        with (
            patch("core.agent.get_agent"),  # skip LLM warmup in lifespan
            # core.executor.agent_executor is a process-wide singleton shared
            # by every test in the session (api/routes/query.py,
            # api/routes/slack.py). The real lifespan shuts it down on exit,
            # and a ThreadPoolExecutor can't be un-shut-down — without this,
            # any other test that submits real work to it after this
            # module's tests finish would fail with "cannot schedule new
            # futures after shutdown".
            patch("core.executor.agent_executor.shutdown"),
        ):
            from importlib import reload

            import api.main as main_mod

            reload(main_mod)  # pick up patched settings
            with TestClient(main_mod.app, raise_server_exceptions=False) as c:
                yield c
    finally:
        settings.APP_DATABASE_URL = orig_app
        settings.DATABASE_URL = orig_erp
        app_engine.cache_clear()
        erp_engine.cache_clear()


_user_counter = 0


@pytest.fixture()
def sso_session(client):
    """Create an ERP person/auth_user row (self-scoped: no group
    memberships) and set a valid Google-SSO session cookie for it on the
    shared client — the only identity /query accepts (api/deps.py's
    get_session_user). Returns the person_id."""
    import sqlalchemy

    from api.auth import SESSION_COOKIE_NAME, create_session_cookie
    from core.config import settings

    global _user_counter
    _user_counter += 1
    person_id = 900 + _user_counter

    engine = sqlalchemy.create_engine(settings.DATABASE_URL)
    with engine.begin() as conn:
        conn.execute(
            sqlalchemy.text("INSERT INTO auth_user (id, is_active) VALUES (:id, 1)"),
            {"id": person_id},
        )
        conn.execute(
            sqlalchemy.text("INSERT INTO person (id, user_id, is_active) VALUES (:id, :id, 1)"),
            {"id": person_id},
        )
    engine.dispose()

    client.cookies.set(SESSION_COOKIE_NAME, create_session_cookie(person_id))
    try:
        yield person_id
    finally:
        client.cookies.delete(SESSION_COOKIE_NAME)


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


class TestHealth:
    def test_health_responds(self, client):
        r = client.get("/health")
        assert r.status_code in (200, 503)


# ---------------------------------------------------------------------------
# Engine pool tuning — pool_pre_ping guards against a proxy-idled connection
# surfacing as a user-visible 500 instead of transparently reconnecting.
# ---------------------------------------------------------------------------


class TestEnginePoolTuning:
    def test_app_engine_has_pool_pre_ping_and_recycle(self, client):
        from api.deps import app_engine

        engine = app_engine()
        assert engine.pool._pre_ping is True
        assert engine.pool._recycle == 300

    def test_erp_engine_has_pool_pre_ping_and_recycle(self, client):
        from api.deps import erp_engine

        engine = erp_engine()
        assert engine.pool._pre_ping is True
        assert engine.pool._recycle == 300


# ---------------------------------------------------------------------------
# /query
# ---------------------------------------------------------------------------


class TestQuery:
    """/query accepts exactly one form of identity: a valid Google-SSO
    session cookie (api/routes/auth.py, api/deps.py::get_session_user). There
    is no slack_user_id field and no admin-Basic-Auth fallback — see
    api/routes/query.py. (The admin panel's own auth and the Slack bot's own
    identity resolution are separate, self-contained flows, exercised by
    TestAdminAuth and adapters/slack.py respectively — neither is wired into
    this endpoint.)"""

    def test_empty_query_rejected(self, client, sso_session):
        r = client.post("/query", json={"query": "   "})
        assert r.status_code == 400

    def test_no_session_cookie_denied(self, client):
        """Regression test for the RBAC fail-open bug: no session cookie
        means no proven identity, so the request must be denied — never
        silently granted full access."""
        r = client.post("/query", json={"query": "How many employees do we have?"})
        assert r.status_code == 401

    def test_valid_query_returns_answer(self, client, sso_session):
        r = client.post("/query", json={"query": "How many employees do we have?"})
        assert r.status_code == 200
        assert _sse_result(r)["answer"] == MOCK_ANSWER

    @pytest.mark.parametrize(
        "query_text",
        [
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
        ],
    )
    def test_canonical_query(self, client, sso_session, query_text):
        """All 20 canonical queries from SPEC.md must return 200 with an answer."""
        r = client.post("/query", json={"query": query_text})
        assert r.status_code == 200
        assert len(_sse_result(r)["answer"]) > 0

    def test_agent_failure_yields_sse_error_event_not_500(self, client, sso_session):
        """By the time the agent call fails, the SSE stream has already sent
        a 200 with headers (the `status` event) — a failure can only be
        reported as an `error` event, not an HTTP 500.

        Patches api.services.query_service.agent_query (not core.agent.query)
        — the `client` fixture's mock_query patch is applied once, at module
        import time, so query_service's `from core.agent import query as
        agent_query` already captured that reference; re-patching
        core.agent.query afterwards wouldn't reach this already-bound name.
        """
        with patch("api.services.query_service.agent_query", side_effect=RuntimeError("boom")):
            r = client.post("/query", json={"query": "How many employees?"})

        assert r.status_code == 200
        events = _sse_events(r)
        names = [name for name, _ in events]
        assert "status" in names
        assert "error" in names
        assert "answer" not in names

    def test_status_event_sent_before_answer(self, client, sso_session):
        r = client.post("/query", json={"query": "How many employees?"})
        names = [name for name, _ in _sse_events(r)]
        assert names.index("status") < names.index("answer")


# ---------------------------------------------------------------------------
# Admin authentication — unauthenticated requests must be rejected
# ---------------------------------------------------------------------------


class TestAdminAuth:
    def test_list_users_without_auth_returns_401(self, client):
        assert client.get("/users").status_code == 401

    def test_post_users_without_auth_returns_401(self, client):
        assert (
            client.post(
                "/users",
                json={
                    "employee_id": 1,
                    "slack_user_id": "U_NOAUTH",
                },
            ).status_code
            == 401
        )

    def test_delete_user_without_auth_returns_401(self, client):
        assert client.delete("/users/1").status_code == 401

    def test_wrong_password_returns_401(self, client):
        import base64

        bad_headers = {
            "Authorization": "Basic " + base64.b64encode(b"test-admin:wrong-password").decode()
        }
        assert client.get("/users", headers=bad_headers).status_code == 401


# ---------------------------------------------------------------------------
# Security headers + CORS
# ---------------------------------------------------------------------------


class TestSecurityHeaders:
    def test_security_headers_present_on_response(self, client):
        r = client.get("/health")
        assert r.headers["x-content-type-options"] == "nosniff"
        assert r.headers["x-frame-options"] == "DENY"
        assert r.headers["referrer-policy"] == "no-referrer"
        assert "max-age=63072000" in r.headers["strict-transport-security"]

    def test_cors_methods_are_narrowed(self, client):
        r = client.options(
            "/query",
            headers={
                "Origin": "http://localhost",
                "Access-Control-Request-Method": "PUT",
            },
        )
        allowed = r.headers.get("access-control-allow-methods", "")
        assert "PUT" not in allowed
        assert "POST" in allowed


# ---------------------------------------------------------------------------
# User admin endpoints
# ---------------------------------------------------------------------------


class TestUserAdmin:
    def test_list_users(self, client):
        r = client.get("/users", headers=_ADMIN_HEADERS)
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_register_user(self, client):
        r = client.post(
            "/users",
            json={
                "employee_id": 777,
                "slack_user_id": "U_ADMIN_TEST",
            },
            headers=_ADMIN_HEADERS,
        )
        assert r.status_code == 201
        data = r.json()
        assert data["employee_id"] == 777
        assert data["slack_user_id"] == "U_ADMIN_TEST"
        # cleanup
        client.delete(f"/users/{data['id']}", headers=_ADMIN_HEADERS)

    def test_duplicate_slack_id_rejected(self, client):
        payload = {"employee_id": 1, "slack_user_id": "U_DUP_TEST"}
        r1 = client.post("/users", json=payload, headers=_ADMIN_HEADERS)
        assert r1.status_code == 201
        r2 = client.post("/users", json=payload, headers=_ADMIN_HEADERS)
        assert r2.status_code == 409
        client.delete(f"/users/{r1.json()['id']}", headers=_ADMIN_HEADERS)

    def test_deregister_user(self, client):
        r = client.post(
            "/users",
            json={
                "employee_id": 555,
                "slack_user_id": "U_DEL_TEST",
            },
            headers=_ADMIN_HEADERS,
        )
        assert r.status_code == 201
        user_id = r.json()["id"]
        assert client.delete(f"/users/{user_id}", headers=_ADMIN_HEADERS).status_code == 204
        users = client.get("/users", headers=_ADMIN_HEADERS).json()
        assert all(u["id"] != user_id for u in users)

    def test_deregister_nonexistent_user(self, client):
        r = client.delete("/users/99999", headers=_ADMIN_HEADERS)
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# Google SSO login — end-to-end through the real app
# ---------------------------------------------------------------------------


class TestGoogleSSOLoginE2E:
    """Authlib's Google round-trip is mocked; everything after it (ERP
    lookup, cookie issuance, /query using that cookie) runs for real against
    the client fixture's test SQLite ERP stand-in."""

    @pytest.fixture
    def sso_user(self, client):
        """Insert an auth_user/person row with a known email, matching the
        e2e fixture's ERP stand-in schema. Uses the file's shared
        _user_counter (like sso_session) since the client fixture is
        module-scoped and multiple tests in this class need distinct IDs to
        avoid a UNIQUE constraint collision on auth_user.id."""
        import sqlalchemy

        from core.config import settings

        global _user_counter
        _user_counter += 1
        auth_user_id = 7000 + _user_counter
        person_id = 8000 + _user_counter
        email = f"sso.tester.{_user_counter}@arbisoft.com"

        engine = sqlalchemy.create_engine(settings.DATABASE_URL)
        with engine.begin() as conn:
            conn.execute(
                sqlalchemy.text(
                    "INSERT INTO auth_user (id, is_active, email) VALUES (:id, 1, :email)"
                ),
                {"id": auth_user_id, "email": email},
            )
            conn.execute(
                sqlalchemy.text(
                    "INSERT INTO person (id, user_id, is_active) VALUES (:pid, :uid, 1)"
                ),
                {"pid": person_id, "uid": auth_user_id},
            )
        engine.dispose()
        return email

    def test_login_then_query_uses_session_cookie(self, client, sso_user):
        from unittest.mock import AsyncMock, patch

        token = {
            "userinfo": {
                "email": sso_user,
                "email_verified": True,
                "hd": "arbisoft.com",
            }
        }
        import api.routes.auth as auth_route

        with patch.object(
            auth_route.oauth.google, "authorize_access_token", new=AsyncMock(return_value=token)
        ):
            callback_response = client.get("/auth/callback")
        assert callback_response.status_code == 200

        r = client.post("/query", json={"query": "How many employees?"})
        assert r.status_code == 200
        assert _sse_result(r)["answer"] == MOCK_ANSWER

    def test_unregistered_email_rejected(self, client):
        from unittest.mock import AsyncMock, patch

        token = {
            "userinfo": {
                "email": "not.in.erp@arbisoft.com",
                "email_verified": True,
                "hd": "arbisoft.com",
            }
        }
        import api.routes.auth as auth_route

        with patch.object(
            auth_route.oauth.google, "authorize_access_token", new=AsyncMock(return_value=token)
        ):
            r = client.get("/auth/callback")
        assert r.status_code == 403

    def test_wrong_domain_rejected(self, client):
        from unittest.mock import AsyncMock, patch

        token = {"userinfo": {"email": "eve@gmail.com", "email_verified": True, "hd": "gmail.com"}}
        import api.routes.auth as auth_route

        with patch.object(
            auth_route.oauth.google, "authorize_access_token", new=AsyncMock(return_value=token)
        ):
            r = client.get("/auth/callback")
        assert r.status_code == 403

    def test_logout_then_query_denied(self, client, sso_user):
        from unittest.mock import AsyncMock, patch

        token = {"userinfo": {"email": sso_user, "email_verified": True, "hd": "arbisoft.com"}}
        import api.routes.auth as auth_route

        with patch.object(
            auth_route.oauth.google, "authorize_access_token", new=AsyncMock(return_value=token)
        ):
            client.get("/auth/callback")
        client.post("/auth/logout")

        # No session cookie means no proven identity — /query has no other
        # form of auth to fall back to, so this must be denied.
        r = client.post("/query", json={"query": "How many employees?"})
        assert r.status_code == 401
