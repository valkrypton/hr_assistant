"""
Business logic for POST /query — RBAC resolution and agent invocation.
Auth (require_admin_unless_open) stays in api/routes/query.py since it's a
FastAPI dependency, not domain logic.

Split into resolve_scope() (fast: validation + a DB lookup) and run_agent()
(slow: the ~15s LLM call) so api/routes/query.py can return a normal HTTP
error status for the fast-path failures and only stream the slow part —
see core/executor.py and api/routes/query.py for how they're composed.

resolve_scope/run_agent take an optional collaborator (repo / resolve_ctx /
agent) for testing (DIP). When omitted they resolve the module globals at
call time, so existing monkeypatching of `agent_query` / `HRUserRepository`
still works.
"""

import structlog
from fastapi import HTTPException
from sqlalchemy.exc import SQLAlchemyError

from api.deps import db_session
from api.schemas.query import QueryRequest
from api.services.agent_runner import default_agent_runner as agent_query
from api.services.interfaces import AgentRunner, ScopeResolver, UserRepo
from core.agent import AgentQueryResult
from core.rbac.context import RBACContext
from core.rbac.repository import HRUserRepository
from core.rbac.resolution import resolve_context

logger = structlog.get_logger(__name__)


def resolve_scope(
    body: QueryRequest,
    repo: UserRepo | None = None,
    resolve_ctx: ScopeResolver | None = None,
) -> RBACContext | None:
    """Validate the request and resolve its RBAC scope. Raises HTTPException
    (400/403/503) for the fast-fail cases — always called before any streaming
    starts, so these still come back as ordinary HTTP error responses."""
    repository = repo if repo is not None else HRUserRepository
    resolver = resolve_ctx if resolve_ctx is not None else resolve_context

    if not body.query.strip():
        raise HTTPException(status_code=400, detail="Query must not be empty.")

    if not body.slack_user_id:
        return None

    # DB work (user lookup) is scoped to its own short session — deliberately
    # NOT held open across the agent call, which can take up to ~15s. Holding
    # one session for the whole request would tie up a pool connection for
    # that entire span instead of just the few DB round-trips that actually
    # need it.
    with db_session() as session:
        hr_user = repository.get_by_slack_user_id(session, body.slack_user_id)

    if not hr_user:
        raise HTTPException(
            status_code=403,
            detail="User not registered. Ask your HR admin to add your Slack account.",
        )

    # employee_id IS person.id — see core/rbac/models.py.
    try:
        ctx = resolver(hr_user.employee_id)
    except SQLAlchemyError as exc:
        logger.warning("rbac_resolution_failed", error=str(exc))
        raise HTTPException(
            status_code=503,
            detail="Authorization service unavailable. Please retry.",
        ) from exc

    if ctx is None:
        raise HTTPException(
            status_code=403,
            detail="Your ERP account is inactive or not provisioned.",
        )
    return ctx


def run_agent(
    query: str,
    rbac_ctx: RBACContext | None,
    agent: AgentRunner | None = None,
) -> AgentQueryResult:
    """The slow part — runs on core.executor.agent_executor, not the request
    thread. Raises on failure; the caller (api/routes/query.py's SSE
    generator) turns that into an `event: error` instead of an HTTP 500,
    since by the time this runs the response has already started streaming."""
    runner = agent if agent is not None else agent_query
    return runner(query, rbac_ctx=rbac_ctx)
