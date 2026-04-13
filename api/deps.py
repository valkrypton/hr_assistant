"""
Shared dependencies for the API layer.

Provides engine factories and the audit-log writer used across multiple routes.
"""
from typing import Optional

import sqlalchemy
from sqlalchemy.orm import Session

from core.config import settings
from core.rbac.models import AuditLog


def app_engine():
    """Writable engine for our own tables (hr_assistant_users, audit logs, etc.)."""
    return sqlalchemy.create_engine(settings.APP_DATABASE_URL)


def erp_engine():
    """Read-only ERP engine — used only for the health check."""
    return sqlalchemy.create_engine(settings.DATABASE_URL)


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
        ))
        session.commit()
