"""POST /query — session-cookie identity is the only auth this route
accepts. Uses a bare FastAPI app + the real router, agent mocked out."""

import json
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.auth import SESSION_COOKIE_NAME, create_session_cookie
from api.routes import query as query_route
from core.agent import AgentQueryResult
from core.rbac.context import RBACContext


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(query_route.router)
    return TestClient(app)


def _sse_result(response):
    for block in response.text.split("\n\n"):
        if block.startswith("event: answer") or block.startswith("event: error"):
            for line in block.splitlines():
                if line.startswith("data:"):
                    return json.loads(line[len("data:") :].strip())
    raise AssertionError(response.text)


def test_session_cookie_used_when_present(client):
    captured = {}

    def fake_resolve_scope_from_session(person_id, resolve_ctx=None):
        captured["person_id"] = person_id
        return RBACContext.unrestricted()

    def fake_run_agent(query, rbac_ctx):
        captured["ctx"] = rbac_ctx
        return AgentQueryResult(
            answer="ok", tables_accessed="", schema_rag_ms=0, agent_ms=0, total_ms=0
        )

    cookie = create_session_cookie(999)
    client.cookies.set(SESSION_COOKIE_NAME, cookie)

    with (
        patch("api.routes.query.resolve_scope_from_session", fake_resolve_scope_from_session),
        patch("api.routes.query.run_agent", fake_run_agent),
    ):
        r = client.post("/query", json={"query": "how many staff?"})

    assert r.status_code == 200
    assert captured["person_id"] == 999
    assert _sse_result(r)["answer"] == "ok"


def test_no_cookie_denied(client):
    r = client.post("/query", json={"query": "how many staff?"})
    assert r.status_code == 401
