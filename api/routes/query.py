from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.deps import app_engine, write_audit
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
def query_endpoint(request: QueryRequest):
    """Accept a natural-language HR question and return an answer from the ERP."""
    if not request.query.strip():
        raise HTTPException(status_code=400, detail="Query must not be empty.")

    rbac_ctx = None
    hr_user = None
    if request.slack_user_id:
        with Session(app_engine()) as session:
            hr_user = (
                session.query(HRUser)
                .filter_by(slack_user_id=request.slack_user_id, is_active=True)
                .first()
            )
        if not hr_user:
            raise HTTPException(status_code=403, detail="Slack user not registered.")
        rbac_ctx = RBACContext.for_user(hr_user)

    try:
        result = agent_query(request.query, rbac_ctx=rbac_ctx)
        write_audit(
            slack_user_id=request.slack_user_id,
            employee_id=hr_user.employee_id if hr_user else None,
            role=hr_user.role if hr_user else None,
            question=request.query,
            answer=result.answer,
            tables_accessed=result.tables_accessed or None,
            schema_rag_ms=result.schema_rag_ms,
            agent_ms=result.agent_ms,
            total_ms=result.total_ms,
        )
        return QueryResponse(answer=result.answer)
    except Exception as exc:
        write_audit(
            slack_user_id=request.slack_user_id,
            employee_id=hr_user.employee_id if hr_user else None,
            role=hr_user.role if hr_user else None,
            question=request.query,
            error=str(exc),
        )
        raise HTTPException(status_code=500, detail=str(exc)) from exc
