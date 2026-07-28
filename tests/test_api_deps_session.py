"""api/deps.py — session-cookie dependency (the sole identity source for
/query)."""

from typing import Annotated

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from api.auth import SESSION_COOKIE_NAME, create_session_cookie
from api.deps import get_session_user


@pytest.fixture
def probe_app():
    app = FastAPI()

    @app.get("/probe")
    def probe(person_id: Annotated[int | None, Depends(get_session_user)]):
        return {"person_id": person_id}

    return TestClient(app)


def test_no_cookie_returns_none(probe_app):
    r = probe_app.get("/probe")
    assert r.json() == {"person_id": None}


def test_valid_cookie_returns_person_id(probe_app):
    cookie = create_session_cookie(77)
    probe_app.cookies.set(SESSION_COOKIE_NAME, cookie)
    r = probe_app.get("/probe")
    assert r.json() == {"person_id": 77}


def test_garbage_cookie_returns_none(probe_app):
    probe_app.cookies.set(SESSION_COOKIE_NAME, "garbage")
    r = probe_app.get("/probe")
    assert r.json() == {"person_id": None}
