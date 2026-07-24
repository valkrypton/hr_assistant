import asyncio
import json
from collections.abc import AsyncIterator

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

from api.deps import OptionalAdminDep
from api.schemas.query import QueryRequest, QueryResponse
from api.services.query_service import resolve_scope, run_agent
from core.executor import agent_executor
from core.rbac.context import RBACContext

router = APIRouter()

_HEARTBEAT_INTERVAL_SECONDS = 5


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


async def _stream_query(query: str, rbac_ctx: RBACContext | None) -> AsyncIterator[str]:
    """
    SSE body for a resolved /query request. Runs the actual agent call on
    core.executor.agent_executor (a bounded pool separate from Starlette's
    default threadpool) so it doesn't compete with routine request handling
    or the Slack webhook path — see core/executor.py.

    Auth and the fast validation/lookup checks (empty query, unregistered
    Slack user) have already run in api/routes/query.py.run_query before
    this generator starts, so those still surface as ordinary HTTP 400/403
    responses. Everything from here on has already sent a 200 with headers,
    so a failure can only be reported as an `error` event, not an HTTP
    status code.
    """
    yield _sse("status", {"stage": "thinking"})

    loop = asyncio.get_running_loop()
    future = loop.run_in_executor(agent_executor, run_agent, query, rbac_ctx)

    # Heartbeat comments (ignored by SSE clients, `: ` prefix) keep proxies
    # and browsers from timing out an idle connection during a slow agent call.
    while True:
        done, _pending = await asyncio.wait({future}, timeout=_HEARTBEAT_INTERVAL_SECONDS)
        if done:
            break
        yield ": heartbeat\n\n"

    try:
        result = future.result()
    except Exception:
        yield _sse("error", {"detail": "Agent error — please try again."})
        return

    yield _sse("answer", QueryResponse(answer=result.answer).model_dump())


@router.post("/query")
async def run_query(body: QueryRequest, admin: OptionalAdminDep) -> StreamingResponse:
    """
    Natural-language HR query endpoint — streamed over Server-Sent Events.

    Requires admin HTTP Basic auth unless ALLOW_UNAUTHENTICATED_QUERY=true
    (local dev). slack_user_id selects the RBAC scope to apply — it is not an
    identity proof; end-user traffic goes through the signed Slack webhook.

    - No slack_user_id: runs without RBAC. Allowed only for an authenticated
      admin or when ALLOW_UNAUTHENTICATED_QUERY=true — require_admin_unless_open
      has already enforced this, so no further auth check is needed here.
    - With slack_user_id: enforces RBAC based on the user's registered role.

    Response is `text/event-stream`: an immediate `status` event, periodic
    `: heartbeat` comments while the agent runs, then exactly one of
    `answer` or `error`.
    """
    # Fast checks (empty query / unregistered Slack user) run on Starlette's
    # default threadpool — quick enough to share it — and raise HTTPException
    # for a normal HTTP 400/403 response before any streaming starts.
    rbac_ctx = await run_in_threadpool(resolve_scope, body)
    return StreamingResponse(_stream_query(body.query, rbac_ctx), media_type="text/event-stream")
