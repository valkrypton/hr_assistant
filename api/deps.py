"""
Shared dependencies for the API layer.

Provides engine factories and the audit-log writer used across multiple routes.
"""
from datetime import datetime, timedelta, timezone
from typing import Optional

import sqlalchemy
from fastapi import HTTPException
from sqlalchemy.orm import Session

from core.config import settings
from core.rbac.models import AuditLog


def app_engine():
    """Writable engine for our own tables (hr_assistant_users, audit logs, etc.)."""
    return sqlalchemy.create_engine(settings.APP_DATABASE_URL)


def erp_engine():
    """Read-only ERP engine — used only for the health check."""
    return sqlalchemy.create_engine(settings.DATABASE_URL)


def check_rate_limit(slack_user_id: str) -> None:
    """
    Raise HTTP 429 if the user has hit RATE_LIMIT_PER_HOUR queries in the
    last 60 minutes. Uses the audit log as the source of truth — no extra
    table needed. Set RATE_LIMIT_PER_HOUR=0 to disable.
    """
    limit = settings.RATE_LIMIT_PER_HOUR
    if limit <= 0:
        return

    since = datetime.now(timezone.utc) - timedelta(hours=1)
    with Session(app_engine()) as session:
        count = (
            session.query(AuditLog)
            .filter(
                AuditLog.slack_user_id == slack_user_id,
                AuditLog.created_at >= since,
            )
            .count()
        )

    if count >= limit:
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded — max {limit} queries per hour. Try again later.",
        )


def write_audit(
    *,
    slack_user_id: Optional[str],
    employee_id: Optional[int],
    role: Optional[str],
    question: str,
    answer: Optional[str] = None,
    tables_accessed: Optional[str] = None,
    error: Optional[str] = None,
    schema_rag_ms: Optional[int] = None,
    agent_ms: Optional[int] = None,
    total_ms: Optional[int] = None,
    prompt_tokens: Optional[int] = None,
    completion_tokens: Optional[int] = None,
    total_tokens: Optional[int] = None,
) -> None:
    """Append one row to the audit log in the app DB (FR-6.1 / FR-6.2)."""
    with Session(app_engine()) as session:
        session.add(AuditLog(
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
        ))
        session.commit()
