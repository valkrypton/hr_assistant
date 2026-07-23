"""
Business logic for POST /query — RBAC resolution and agent invocation.
Auth (require_admin_unless_open) stays in api/routes/query.py since it's a
FastAPI dependency, not domain logic.
"""

from fastapi import HTTPException

from api.deps import db_session
from api.schemas.query import QueryRequest, QueryResponse
from core.agent import query as agent_query
from core.rbac.context import RBACContext
from core.rbac.repository import HRUserRepository


def run_query(body: QueryRequest) -> QueryResponse:
    if not body.query.strip():
        raise HTTPException(status_code=400, detail="Query must not be empty.")

    rbac_ctx = None

    # DB work (user lookup) is scoped to its own short session — deliberately
    # NOT held open across the agent_query() call below, which can take up
    # to ~15s. Holding one session for the whole request would tie up a pool
    # connection for that entire span instead of just the few DB round-trips
    # that actually need it.
    if body.slack_user_id:
        with db_session() as session:
            hr_user = HRUserRepository.get_by_slack_user_id(session, body.slack_user_id)

        if not hr_user:
            raise HTTPException(
                status_code=403,
                detail="User not registered. Ask your HR admin to add your Slack account.",
            )

        rbac_ctx = RBACContext.for_user(hr_user)

    try:
        result = agent_query(body.query, rbac_ctx=rbac_ctx)
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Agent error — please try again.") from exc

    return QueryResponse(answer=result.answer)
