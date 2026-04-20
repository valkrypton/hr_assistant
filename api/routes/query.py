from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.deps import app_engine, check_rate_limit, write_audit
from core.agent import query as agent_query
from core.rbac.context import RBACContext
from core.rbac.models import HRUser

router = APIRouter()


class QueryRequest(BaseModel):
    query: str
    slack_user_id: Optional[str] = None


class QueryResponse(BaseModel):
    answer: str


@router.post("/query", response_model=QueryResponse)
def run_query(body: QueryRequest):
    """
    Natural-language HR query endpoint.

    - No slack_user_id: runs without RBAC (open access, useful for local testing).
    - With slack_user_id: enforces RBAC based on the user's registered role.
    """
    if not body.query.strip():
        raise HTTPException(status_code=400, detail="Query must not be empty.")

    rbac_ctx = None
    employee_id = None
    role = None

    if body.slack_user_id:
        check_rate_limit(body.slack_user_id)

        with Session(app_engine()) as session:
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
        write_audit(
            slack_user_id=body.slack_user_id,
            employee_id=employee_id,
            role=role,
            question=body.query,
            error=str(exc),
        )
        raise HTTPException(status_code=500, detail="Agent error — please try again.") from exc

    write_audit(
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
