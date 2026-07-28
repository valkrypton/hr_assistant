"""
HR Assistant — API layer (FastAPI).

This file owns only app setup: lifespan, middleware, admin panel, and router
registration.  All route logic lives in api/routes/.
"""

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from sqladmin import Admin
from sqladmin.authentication import AuthenticationBackend
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.requests import Request as StarletteRequest
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from api.admin import HRUserAdmin
from api.auth import verify_password
from api.deps import app_engine
from api.routes import auth, health, query, slack, users
from core.agent import get_agent
from core.config import settings
from core.executor import agent_executor
from core.logging import configure_logging
from core.rbac.models import AdminUser, Base

configure_logging()


# ---------------------------------------------------------------------------
# SQLAdmin authentication backend
# ---------------------------------------------------------------------------


class AdminAuth(AuthenticationBackend):
    async def login(self, request: StarletteRequest) -> bool:
        form = await request.form()
        username = form.get("username", "")
        password = form.get("password", "")

        def _lookup():
            with Session(app_engine()) as session:
                return session.query(AdminUser).filter_by(username=username, is_active=True).first()

        admin = await run_in_threadpool(_lookup)
        if admin and verify_password(password, admin.hashed_password):
            request.session["admin_username"] = username
            return True
        return False

    async def logout(self, request: StarletteRequest) -> bool:
        request.session.clear()
        return True

    async def authenticate(self, request: StarletteRequest) -> bool:
        return "admin_username" in request.session


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.DEBUG:
        # Zero-config local dev only — creates tables if they don't exist yet.
        # In production, schema changes go through Alembic (migrations/),
        # which is the source of truth: `alembic upgrade head`. Running
        # create_all() there can leave tables without an alembic_version,
        # causing later migrations to fail or drift.
        Base.metadata.create_all(app_engine())
    get_agent()  # warm up the shared unrestricted agent on startup
    yield
    agent_executor.shutdown(wait=True)


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="HR Assistant API",
    description="Natural-language interface to company HR/ERP data.",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(ProxyHeadersMiddleware, trusted_hosts=settings.TRUSTED_PROXY_HOSTS)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allow_origins,
    allow_credentials=True,
    # Only the methods/headers the API actually uses — the admin panel is
    # browsed same-origin and isn't affected by CORS at all.
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type", "Authorization"],
)

# Required by Authlib's Starlette OAuth client, which stores the OAuth
# state/nonce in request.session during the login redirect round-trip
# (api/routes/auth.py). Separate from SQLAdmin's own internal
# SessionMiddleware, which sqladmin.Admin() scopes only to its /admin
# sub-app — this one covers the rest of the app (/auth/*).
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.SECRET_KEY.get_secret_value(),
    same_site="lax",
    https_only=not settings.DEBUG,
)

_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    # Harmless over plain HTTP (browsers ignore it); real value once behind
    # TLS termination in production.
    "Strict-Transport-Security": "max-age=63072000; includeSubDomains",
}


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        for header, value in _SECURITY_HEADERS.items():
            response.headers.setdefault(header, value)
        return response


app.add_middleware(SecurityHeadersMiddleware)

_ADMIN_CSS = b"""<style>
  .table-responsive { overflow-x: hidden !important; }
  .table td { max-width: 220px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
</style></head>"""


class AdminCSSMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/admin") and "text/html" in response.headers.get(
            "content-type", ""
        ):
            body = b"".join([chunk async for chunk in response.body_iterator])
            body = body.replace(b"</head>", _ADMIN_CSS)
            modified = HTMLResponse(
                content=body.decode(),
                status_code=response.status_code,
                media_type=response.media_type,
                background=response.background,
            )
            preserved = [
                (k, v)
                for k, v in response.raw_headers
                if k.lower() not in (b"content-length", b"content-type")
            ]
            modified.raw_headers = preserved + list(modified.raw_headers)
            return modified
        return response


app.add_middleware(AdminCSSMiddleware)


# ---------------------------------------------------------------------------
# SQLAdmin panel  →  http://localhost:8000/admin
# ---------------------------------------------------------------------------

admin = Admin(
    app,
    engine=app_engine(),
    authentication_backend=AdminAuth(
        secret_key=settings.SECRET_KEY.get_secret_value(),
        https_only=not settings.DEBUG,
        same_site="lax",
    ),
)
admin.add_view(HRUserAdmin)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

app.include_router(health.router)
app.include_router(auth.router)
app.include_router(query.router)
app.include_router(users.router)
app.include_router(slack.router)

# Serves index.html same-origin with the API — avoids the CORS/SameSite
# dev friction of opening it via file:// or a separate static server (the
# Google-SSO session cookie is SameSite=Lax, so it's only ever attached to
# same-site requests; file:// has no site of its own to match).
_INDEX_HTML_PATH = Path(__file__).resolve().parent.parent / "index.html"


@app.get("/", include_in_schema=False)
async def serve_index() -> FileResponse:
    return FileResponse(_INDEX_HTML_PATH)


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    config = uvicorn.Config("api.main:app", host="0.0.0.0", port=8000)
    server = uvicorn.Server(config)
    asyncio.run(server.serve())
