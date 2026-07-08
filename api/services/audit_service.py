"""Business logic for GET /audit — filter/query building over AuditLog."""
from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from api.schemas.audit import AuditLogResponse
from core.rbac.models import AuditLog


def _parse_date(value: str, param: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid date format for {param}: '{value}'. Use ISO-8601 (e.g. 2025-01-01).",
        )
    # AuditLog.created_at is timezone-aware (TIMESTAMPTZ); treat inputs with
    # no offset (e.g. "2025-01-01") as UTC so filtering is deterministic
    # instead of depending on DB/session timezone comparison behavior.
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def get_audit_logs(
    session: Session,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    slack_user_id: Optional[str] = None,
    role: Optional[str] = None,
    limit: int = 100,
) -> list[AuditLogResponse]:
    # Clamp negatives to 0 (which yields an empty list) and cap the upper bound.
    limit = max(0, min(limit, 1000))

    parsed_from = _parse_date(from_date, "from_date") if from_date else None
    parsed_to = _parse_date(to_date, "to_date") if to_date else None

    q = session.query(AuditLog)
    if parsed_from:
        q = q.filter(AuditLog.created_at >= parsed_from)
    if parsed_to:
        q = q.filter(AuditLog.created_at <= parsed_to)
    if slack_user_id:
        q = q.filter(AuditLog.slack_user_id == slack_user_id)
    if role:
        q = q.filter(AuditLog.role == role)
    rows = q.order_by(AuditLog.created_at.desc()).limit(limit).all()

    return [
        AuditLogResponse(
            id=r.id,
            created_at=r.created_at.isoformat(),
            slack_user_id=r.slack_user_id,
            employee_id=r.employee_id,
            role=r.role,
            question=r.question,
            answer=r.answer,
            tables_accessed=r.tables_accessed,
            error=r.error,
            schema_rag_ms=r.schema_rag_ms,
            agent_ms=r.agent_ms,
            total_ms=r.total_ms,
            prompt_tokens=r.prompt_tokens,
            completion_tokens=r.completion_tokens,
            total_tokens=r.total_tokens,
        )
        for r in rows
    ]
