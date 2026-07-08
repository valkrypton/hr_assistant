"""
Business logic for POST /query — RBAC resolution, agent invocation, and
audit logging. Auth (require_admin_unless_open) stays in api/routes/query.py
since it's a FastAPI dependency, not domain logic.
"""

from fastapi import HTTPException

from api.deps import check_rate_limit, db_session, write_audit
from api.schemas.query import QueryRequest, QueryResponse
from core.agent import query as agent_query
from core.rbac.context import RBACContext
from core.rbac.models import HRUser


def run_query(body: QueryRequest) -> QueryResponse:
    if not body.query.strip():
        raise HTTPException(status_code=400, detail="Query must not be empty.")

    rbac_ctx = None
    employee_id = None
    role = None

    # DB work (rate-limit check + user lookup) is scoped to its own short
    # session — deliberately NOT held open across the agent_query() call
    # below, which can take up to ~15s. Holding one session for the whole
    # request would tie up a pool connection for that entire span instead of
    # just the few DB round-trips that actually need it.
    if body.slack_user_id:
        with db_session() as session:
            check_rate_limit(session, body.slack_user_id)

            hr_user = (
                session.query(HRUser)
                .filter_by(slack_user_id=body.slack_user_id, is_active=True)
                .first()
            )

        if not hr_user:
            raise HTTPException(
                status_code=403,
                detail="User not registered. Ask your HR admin to add your Slack account.",
            )

        rbac_ctx = RBACContext.for_user(hr_user)
        employee_id = hr_user.employee_id
        role = hr_user.role

    try:
        result = agent_query(body.query, rbac_ctx=rbac_ctx)
    except Exception as exc:
        with db_session() as session:
            write_audit(
                session,
                slack_user_id=body.slack_user_id,
                employee_id=employee_id,
                role=role,
                question=body.query,
                error=str(exc),
            )
        raise HTTPException(status_code=500, detail="Agent error — please try again.") from exc

    with db_session() as session:
        write_audit(
            session,
            slack_user_id=body.slack_user_id,
            employee_id=employee_id,
            role=role,
            question=body.query,
            answer=result.answer,
            tables_accessed=result.tables_accessed or None,
            schema_rag_ms=result.schema_rag_ms,
            agent_ms=result.agent_ms,
            total_ms=result.total_ms,
            prompt_tokens=result.prompt_tokens or None,
            completion_tokens=result.completion_tokens or None,
            total_tokens=result.total_tokens or None,
        )

    return QueryResponse(answer=result.answer)
