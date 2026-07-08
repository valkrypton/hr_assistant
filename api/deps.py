"""
Shared dependencies for the API layer.

The engine factories, DB-session helper, and audit-log writer now live in
core/ (core.db, core.telemetry.audit) so the messaging adapters can share them
without importing api/. They are re-exported here for the existing API call
sites. This module keeps the FastAPI-only pieces: HTTP Basic auth guards, the
request-scoped session dependency, and the HTTP rate-limit check.
"""

from collections.abc import Iterator
from functools import lru_cache
from typing import Annotated

from fastapi import Depends, HTTPException, Security
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from sqlalchemy.orm import Session

from core.auth import hash_password, verify_password
from core.config import settings
from core.db import app_engine, db_session, erp_engine
from core.rate_limit import count_recent_queries
from core.rbac.models import AdminUser
from core.telemetry.audit import write_audit

# Re-exported for API call sites that import these names from api.deps.
__all__ = [
    "app_engine",
    "erp_engine",
    "db_session",
    "write_audit",
    "get_db",
    "DbDep",
    "require_admin",
    "require_admin_unless_open",
    "check_rate_limit",
]

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


def require_admin_unless_open(
    credentials: HTTPBasicCredentials | None = Security(_basic_auth),
) -> AdminUser | None:
    """
    /query guard. slack_user_id in the request body selects an RBAC scope but
    is NOT proof of identity (Slack IDs are public within a workspace), so the
    request must be vouched for by admin credentials — unless
    ALLOW_UNAUTHENTICATED_QUERY explicitly opts into open access (local dev).
    Production RBAC traffic goes through the signature-verified Slack webhook.
    """
    if settings.ALLOW_UNAUTHENTICATED_QUERY:
        return None
    return require_admin(credentials)


def get_db() -> Iterator[Session]:
    """FastAPI dependency — one Session per request, shared by every DB call
    the route makes instead of each opening its own."""
    with db_session() as session:
        yield session


DbDep = Annotated[Session, Depends(get_db)]


def check_rate_limit(session: Session, slack_user_id: str) -> None:
    """
    Raise HTTP 429 if the user has hit RATE_LIMIT_PER_HOUR queries in the
    last 60 minutes. Uses the audit log as the source of truth — no extra
    table needed. Set RATE_LIMIT_PER_HOUR=0 to disable.
    """
    limit = settings.RATE_LIMIT_PER_HOUR
    if limit <= 0:
        return

    count = count_recent_queries(session, slack_user_id)

    if count >= limit:
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded — max {limit} queries per hour. Try again later.",
        )
