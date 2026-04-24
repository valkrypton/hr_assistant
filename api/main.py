"""
HR Assistant — API layer (FastAPI).

This file owns only app setup: lifespan, middleware, admin panel, and router
registration.  All route logic lives in api/routes/.
"""
import asyncio
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from sqladmin import Admin
from sqladmin.authentication import AuthenticationBackend
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request as StarletteRequest
from starlette.concurrency import run_in_threadpool
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from api.admin import AuditLogAdmin, HRUserAdmin
from api.deps import app_engine
from api.routes import audit, health, query, slack, users
from core.agent import get_agent
from core.auth import verify_password
from core.config import settings
from core.rbac.models import AdminUser, Base


# ---------------------------------------------------------------------------
# SQLAdmin authentication backend
# ---------------------------------------------------------------------------

class AdminAuth(AuthenticationBackend):
    async def login(self, request: StarletteRequest) -> bool:
        form = await request.form()
        username = form.get("username", "")
        password = form.get("password", "")

        def _lookup():
            from sqlalchemy.orm import Session
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
    Base.metadata.create_all(app_engine())
    get_agent()  # warm up the shared unrestricted agent on startup
    yield


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
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_ADMIN_CSS = b"""<style>
  .table-responsive { overflow-x: hidden !important; }
  .table td { max-width: 220px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
</style></head>"""


class AdminCSSMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        if (
            request.url.path.startswith("/admin")
            and "text/html" in response.headers.get("content-type", "")
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

admin = Admin(app, engine=app_engine(), authentication_backend=AdminAuth(secret_key=settings.SECRET_KEY))
admin.add_view(HRUserAdmin)
admin.add_view(AuditLogAdmin)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

app.include_router(health.router)
app.include_router(query.router)
app.include_router(users.router)
app.include_router(audit.router)
app.include_router(slack.router)


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    config = uvicorn.Config("api.main:app", host="0.0.0.0", port=8000)
    server = uvicorn.Server(config)
    asyncio.run(server.serve())
