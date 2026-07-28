"""
Shared dependencies for the API layer.

Provides engine factories and the DB-session dependency used across
multiple routes.
"""

from collections.abc import Iterator
from functools import lru_cache
from typing import Annotated

from fastapi import Depends, HTTPException, Request, Security
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from sqlalchemy.orm import Session

from api.auth import (
    SESSION_COOKIE_NAME,
    hash_password,
    read_session_cookie,
    verify_password,
)
from core.db import app_engine, db_session, erp_engine
from core.rbac.models import AdminUser

# app_engine/erp_engine/db_session now live in core/db.py (adapters/slack.py
# needs them too, and adapters/ can't import from api/) — re-exported here
# so existing `from api.deps import app_engine` call sites (api/main.py,
# tests/test_e2e.py) and get_db() below keep working unchanged. __all__
# marks the pass-through names as intentionally re-exported, not dead
# imports.
__all__ = ["app_engine", "db_session", "erp_engine"]

_basic_auth = HTTPBasic(auto_error=False)


@lru_cache(maxsize=1)
def _dummy_hash() -> str:
    return hash_password("timing-guard-placeholder")


def require_admin(credentials: HTTPBasicCredentials | None = Security(_basic_auth)) -> AdminUser:
    """FastAPI dependency — HTTP Basic Auth checked against the AdminUser table."""
    if not credentials:
        raise HTTPException(
            status_code=401,
            detail="Admin authentication required.",
            headers={"WWW-Authenticate": "Basic"},
        )
    with Session(app_engine()) as session:
        admin = (
            session.query(AdminUser)
            .filter_by(username=credentials.username, is_active=True)
            .first()
        )
    # Always run verify_password (even for unknown users) to prevent
    # timing-based username enumeration.
    candidate_hash = admin.hashed_password if admin else _dummy_hash()
    password_ok = verify_password(credentials.password, candidate_hash)
    if not admin or not password_ok:
        raise HTTPException(
            status_code=401,
            detail="Invalid credentials.",
            headers={"WWW-Authenticate": "Basic"},
        )
    return admin


def get_session_user(request: Request) -> int | None:
    """Resolve the requester's person_id from the session cookie set by
    /auth/callback, or None if absent/invalid/expired."""
    cookie = request.cookies.get(SESSION_COOKIE_NAME)
    if cookie is None:
        return None
    return read_session_cookie(cookie)


SessionUserDep = Annotated[int | None, Depends(get_session_user)]


def get_db() -> Iterator[Session]:
    """FastAPI dependency — one Session per request, shared by every DB call
    the route makes instead of each opening its own."""
    with db_session() as session:
        yield session


DbDep = Annotated[Session, Depends(get_db)]
