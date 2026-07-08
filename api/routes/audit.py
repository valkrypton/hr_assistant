from fastapi import APIRouter, Depends

from api.deps import DbDep, require_admin
from api.schemas.audit import AuditLogResponse
from api.services import audit_service

router = APIRouter(dependencies=[Depends(require_admin)])


@router.get("/audit", response_model=list[AuditLogResponse])
def get_audit_logs(
    session: DbDep,
    from_date: str | None = None,
    to_date: str | None = None,
    slack_user_id: str | None = None,
    role: str | None = None,
    limit: int = 100,
):
    """
    Retrieve audit log entries. Supports filtering by date range, user, and role.

    - from_date / to_date: ISO-8601 date strings (e.g. 2025-01-01)
    - slack_user_id: filter to a specific Slack user
    - role: filter to a specific role (cto_ceo, hr_manager, dept_head, team_lead)
    - limit: max rows to return (default 100, max 1000; 0 returns an empty list)
    """
    return audit_service.get_audit_logs(
        session,
        from_date=from_date,
        to_date=to_date,
        slack_user_id=slack_user_id,
        role=role,
        limit=limit,
    )
