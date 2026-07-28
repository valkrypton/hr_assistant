"""
Business logic for POST /query — RBAC resolution and agent invocation.
Identity (the Google-SSO session cookie) is resolved in api/deps.py, a
FastAPI dependency, not domain logic.

Split into resolve_scope_from_session() (fast: an ERP lookup) and run_agent()
(slow: the ~15s LLM call) so api/routes/query.py can return a normal HTTP
error status for the fast-path failures and only stream the slow part —
see core/executor.py and api/routes/query.py for how they're composed.

Both take an optional collaborator (resolve_ctx / agent) for testing (DIP).
When omitted they resolve the module globals at call time, so existing
monkeypatching of `agent_query` still works.
"""

import structlog
from fastapi import HTTPException
from sqlalchemy.exc import SQLAlchemyError

from api.services.agent_runner import default_agent_runner as agent_query
from api.services.interfaces import AgentRunner, ScopeResolver
from core.agent import AgentQueryResult
from core.rbac.context import RBACContext
from core.rbac.resolution import resolve_context

logger = structlog.get_logger(__name__)


def resolve_scope_from_session(
    person_id: int,
    resolve_ctx: ScopeResolver | None = None,
) -> RBACContext:
    """RBAC resolution for a request carrying a valid Google-SSO session
    cookie. Identity is already proven by the cookie (api/deps.py's
    get_session_user); this only resolves the access level. Fails closed on
    either an ERP error (503) or no active identity (403)."""
    resolver = resolve_ctx if resolve_ctx is not None else resolve_context
    try:
        ctx = resolver(person_id)
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
