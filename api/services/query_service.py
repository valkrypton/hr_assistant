"""Business logic for POST /query — delegates to the core execution pipeline
and maps core errors to HTTP status codes. Auth (require_admin_unless_open)
stays in api/routes/query.py since it's a FastAPI dependency, not domain logic.
"""

from fastapi import HTTPException

from api.schemas.query import QueryRequest, QueryResponse
from core.errors import MissingRole, RateLimitExceeded, UserNotRegistered
from core.execution import run_query as run_pipeline


def run_query(body: QueryRequest) -> QueryResponse:
    if not body.query.strip():
        raise HTTPException(status_code=400, detail="Query must not be empty.")

    try:
        result = run_pipeline(body.slack_user_id, body.query)
    except (UserNotRegistered, MissingRole) as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except RateLimitExceeded as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Agent error — please try again.") from exc

    return QueryResponse(answer=result.answer)
