"""GET /auth/login, GET /auth/callback, POST /auth/logout.

Authlib's actual Google round-trip is mocked out (authorize_redirect,
authorize_access_token) — these tests exercise this app's own logic: the
hd/email_verified checks, the ERP lookup, and cookie issuance.
"""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from api.auth import SESSION_COOKIE_NAME, read_session_cookie
from api.routes import auth as auth_route
from core.rbac.erp_identity import ErpIdentity


@pytest.fixture
def client():
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="test-only-not-for-production")
    app.include_router(auth_route.router)
    return TestClient(app, follow_redirects=False)


def test_login_redirects_to_google(client):
    with patch.object(
        auth_route.oauth.google, "authorize_redirect", new=AsyncMock()
    ) as mock_redirect:
        from starlette.responses import RedirectResponse

        mock_redirect.return_value = RedirectResponse("https://accounts.google.com/fake", 302)
        r = client.get("/auth/login")
    assert r.status_code == 302
    assert mock_redirect.call_args.kwargs.get("hd") == "arbisoft.com"


def test_callback_rejects_wrong_domain(client):
    token = {"userinfo": {"email": "eve@gmail.com", "email_verified": True, "hd": "gmail.com"}}
    with patch.object(
        auth_route.oauth.google, "authorize_access_token", new=AsyncMock(return_value=token)
    ):
        r = client.get("/auth/callback")
    assert r.status_code == 403
    assert SESSION_COOKIE_NAME not in r.cookies


def test_callback_rejects_unverified_email(client):
    token = {
        "userinfo": {"email": "alice@arbisoft.com", "email_verified": False, "hd": "arbisoft.com"}
    }
    with patch.object(
        auth_route.oauth.google, "authorize_access_token", new=AsyncMock(return_value=token)
    ):
        r = client.get("/auth/callback")
    assert r.status_code == 403
    assert SESSION_COOKIE_NAME not in r.cookies


def test_callback_not_registered_in_erp(client):
    token = {
        "userinfo": {"email": "alice@arbisoft.com", "email_verified": True, "hd": "arbisoft.com"}
    }
    with (
        patch.object(
            auth_route.oauth.google, "authorize_access_token", new=AsyncMock(return_value=token)
        ),
        patch("api.routes.auth.get_resolver") as mock_get_resolver,
    ):
        mock_get_resolver.return_value.by_email.return_value = None
        r = client.get("/auth/callback")
    assert r.status_code == 403
    assert SESSION_COOKIE_NAME not in r.cookies
    assert "not registered" in r.text.lower()


def test_callback_success_sets_cookie(client):
    token = {
        "userinfo": {"email": "alice@arbisoft.com", "email_verified": True, "hd": "arbisoft.com"}
    }
    identity = ErpIdentity(person_id=100, auth_user_id=500, group_ids=frozenset())
    with (
        patch.object(
            auth_route.oauth.google, "authorize_access_token", new=AsyncMock(return_value=token)
        ),
        patch("api.routes.auth.get_resolver") as mock_get_resolver,
    ):
        mock_get_resolver.return_value.by_email.return_value = identity
        r = client.get("/auth/callback")
    assert r.status_code == 200
    assert SESSION_COOKIE_NAME in r.cookies
    assert read_session_cookie(r.cookies[SESSION_COOKIE_NAME]) == 100


def test_callback_erp_error_returns_503(client):
    token = {
        "userinfo": {"email": "alice@arbisoft.com", "email_verified": True, "hd": "arbisoft.com"}
    }
    from sqlalchemy.exc import SQLAlchemyError

    with (
        patch.object(
            auth_route.oauth.google, "authorize_access_token", new=AsyncMock(return_value=token)
        ),
        patch("api.routes.auth.get_resolver") as mock_get_resolver,
    ):
        mock_get_resolver.return_value.by_email.side_effect = SQLAlchemyError("down")
        r = client.get("/auth/callback")
    assert r.status_code == 503


def test_logout_clears_cookie(client):
    r = client.post("/auth/logout")
    assert r.status_code == 200
    set_cookie = r.headers.get("set-cookie", "")
    assert SESSION_COOKIE_NAME in set_cookie
    # A cleared cookie is set with an immediate/expired Max-Age.
    assert "Max-Age=0" in set_cookie or "max-age=0" in set_cookie.lower()
