"""Google Workspace SSO login — GET /auth/login, GET /auth/callback,
POST /auth/logout.

Restricted to api.config.auth_settings.GOOGLE_WORKSPACE_DOMAIN, enforced
server-side against the ID token's `hd` claim (Google only includes `hd`
for real Workspace accounts, so a personal gmail.com login simply won't
carry it — this is a real check, not just a login-screen hint).

No server-side session store: a successful login signs a stateless cookie
carrying only person_id (api/auth.py). Revocation freshness comes from
core.rbac.resolution.resolve_context's existing RBAC_CACHE_TTL_SECONDS
re-check of person.is_active on every /query call, not from anything here.
"""

import structlog
from authlib.integrations.starlette_client import OAuth
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.exc import SQLAlchemyError

from api.auth import SESSION_COOKIE_NAME, create_session_cookie
from api.config import auth_settings
from core.rbac.resolution import get_resolver

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

oauth = OAuth()
oauth.register(
    name="google",
    client_id=auth_settings.GOOGLE_CLIENT_ID,
    client_secret=auth_settings.GOOGLE_CLIENT_SECRET.get_secret_value(),
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email"},
)


@router.get("/login")
async def login(request: Request):
    """Redirect to Google. hd=<domain> narrows the account chooser to the
    company Workspace — a UI hint only; the real enforcement is in
    /auth/callback checking the returned ID token's hd claim."""
    redirect_uri = request.url_for("auth_callback")
    return await oauth.google.authorize_redirect(
        request, redirect_uri, hd=auth_settings.GOOGLE_WORKSPACE_DOMAIN
    )


def _reject(reason: str) -> HTMLResponse:
    logger.warning("sso_login_rejected", reason=reason)
    return HTMLResponse(f"<p>{reason}</p>", status_code=403)


@router.get("/callback", name="auth_callback")
async def callback(request: Request):
    token = await oauth.google.authorize_access_token(request)
    claims = token.get("userinfo", {})

    email = claims.get("email")
    if not email or not claims.get("email_verified"):
        return _reject("Your Google account's email is not verified.")

    if claims.get("hd") != auth_settings.GOOGLE_WORKSPACE_DOMAIN:
        return _reject(f"Please log in with your {auth_settings.GOOGLE_WORKSPACE_DOMAIN} account.")

    try:
        identity = get_resolver().by_email(email)
    except SQLAlchemyError as exc:
        logger.warning("sso_erp_lookup_failed", error=str(exc))
        return HTMLResponse(
            "<p>Can't verify your account right now. Please try again shortly.</p>",
            status_code=503,
        )

    if identity is None:
        return _reject("You're not registered in the ERP. Contact HR.")

    cookie_value = create_session_cookie(identity.person_id)

    if auth_settings.POST_LOGIN_REDIRECT_URL:
        response = RedirectResponse(auth_settings.POST_LOGIN_REDIRECT_URL, status_code=302)
    else:
        response = HTMLResponse("<p>Logged in. You can close this tab.</p>")

    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=cookie_value,
        max_age=auth_settings.SESSION_COOKIE_MAX_AGE_SECONDS,
        httponly=True,
        samesite="lax",
    )
    return response


@router.post("/logout")
async def logout():
    response = HTMLResponse("<p>Logged out.</p>")
    response.delete_cookie(SESSION_COOKIE_NAME)
    return response
