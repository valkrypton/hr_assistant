"""
Shared dependencies for the API layer.

Provides engine factories, the DB-session dependency, and the audit-log
writer used across multiple routes.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache
from typing import Annotated

import sqlalchemy
from fastapi import Depends, HTTPException, Security
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from sqlalchemy.orm import Session

from core.auth import hash_password, verify_password
from core.config import settings
from core.rate_limit import count_recent_queries
from core.rbac.models import AdminUser, AuditLog

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


@lru_cache(maxsize=1)
def app_engine():
    """Writable engine for our own tables (hr_assistant_users, audit logs, etc.)."""
    return sqlalchemy.create_engine(settings.APP_DATABASE_URL)


@lru_cache(maxsize=1)
def erp_engine():
    """Read-only ERP engine — used only for the health check."""
    return sqlalchemy.create_engine(settings.DATABASE_URL)


@contextmanager
def db_session() -> Iterator[Session]:
    """Open a Session on the app engine. Usable outside a request (e.g. the
    Slack background task), unlike get_db() below which is FastAPI-only."""
    with Session(app_engine()) as session:
        yield session


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


def write_audit(
    session: Session,
    *,
    slack_user_id: str | None,
    employee_id: int | None,
    role: str | None,
    question: str,
    answer: str | None = None,
    tables_accessed: str | None = None,
    error: str | None = None,
    schema_rag_ms: int | None = None,
    agent_ms: int | None = None,
    total_ms: int | None = None,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    total_tokens: int | None = None,
) -> None:
    """Append one row to the audit log in the app DB (FR-6.1 / FR-6.2)."""
    session.add(
        AuditLog(
            slack_user_id=slack_user_id,
            employee_id=employee_id,
            role=role,
            question=question,
            answer=answer,
            tables_accessed=tables_accessed,
            error=error,
            schema_rag_ms=schema_rag_ms,
            agent_ms=agent_ms,
            total_ms=total_ms,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
        )
    )
    session.commit()
