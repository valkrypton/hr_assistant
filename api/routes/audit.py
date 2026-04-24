from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.deps import app_engine, require_admin
from core.rbac.models import AuditLog

router = APIRouter(dependencies=[Depends(require_admin)])


class AuditLogResponse(BaseModel):
    id: int
    created_at: str
    slack_user_id: Optional[str]
    employee_id: Optional[int]
    role: Optional[str]
    question: str
    answer: Optional[str]
    tables_accessed: Optional[str]
    error: Optional[str]
    schema_rag_ms: Optional[int]
    agent_ms: Optional[int]
    total_ms: Optional[int]
    prompt_tokens: Optional[int]
    completion_tokens: Optional[int]
    total_tokens: Optional[int]


@router.get("/audit", response_model=list[AuditLogResponse])
def get_audit_logs(
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    slack_user_id: Optional[str] = None,
    role: Optional[str] = None,
    limit: int = 100,
):
    """
    Retrieve audit log entries. Supports filtering by date range, user, and role.

    - from_date / to_date: ISO-8601 date strings (e.g. 2025-01-01)
    - slack_user_id: filter to a specific Slack user
    - role: filter to a specific role (cto_ceo, hr_manager, dept_head, team_lead)
    - limit: max rows to return (default 100, max 1000)
    """
    limit = min(limit, 1000)

    def _parse_date(value: str, param: str) -> datetime:
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid date format for {param}: '{value}'. Use ISO-8601 (e.g. 2025-01-01).")

    parsed_from = _parse_date(from_date, "from_date") if from_date else None
    parsed_to = _parse_date(to_date, "to_date") if to_date else None

    with Session(app_engine()) as session:
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
