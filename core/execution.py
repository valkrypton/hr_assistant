"""The query execution pipeline.

One framework-neutral place that sequences a query end to end:

    rate limit -> identity -> runtime -> security validation (redact) -> audit

The API path calls run_query() directly. The Slack adapter composes the same
core steps around its own Slack I/O (thread history, Block Kit, per-failure
messages) rather than calling run_query(), because those calls interleave with
the agent run — but it uses the same runtime, redaction, and audit helpers, so
the enforcement lives in one place.

Raises the typed errors from core.errors (UserNotRegistered, MissingRole,
RateLimitExceeded); callers map them to their transport (HTTP status / Slack
message). Non-typed runtime failures are audited here and re-raised.
"""

from __future__ import annotations

from core.db import db_session
from core.identity.resolver import resolve
from core.rate_limit import enforce_rate_limit
from core.runtimes import AgentRunRequest, AgentRunResult, get_runtime
from core.telemetry.audit import write_audit


def run_query(
    slack_user_id: str | None,
    question: str,
    *,
    conversation_history: list[dict] | None = None,
    extra_audit: dict | None = None,
) -> AgentRunResult:
    """Resolve the requester, run the agent, redact, and audit.

    slack_user_id=None runs unscoped (admin/open mode) — no identity or rate
    check, no output redaction, still audited.
    """
    extra_audit = extra_audit or {}
    rbac_ctx = None
    employee_id = None
    role = None

    # Identity + rate limit in one short session, released before the ~15s
    # runtime call so a pool connection isn't parked idle across it.
    if slack_user_id:
        with db_session() as session:
            enforce_rate_limit(session, slack_user_id)
            ctx = resolve(session, slack_user_id)
        rbac_ctx = ctx.rbac
        employee_id = ctx.employee_id
        role = ctx.role.value

    runtime = get_runtime()
    try:
        result = runtime.run(
            AgentRunRequest(
                question=question,
                rbac_ctx=rbac_ctx,
                conversation_history=conversation_history,
            )
        )
    except Exception as exc:
        with db_session() as session:
            write_audit(
                session,
                slack_user_id=slack_user_id,
                employee_id=employee_id,
                role=role,
                question=question,
                error=str(exc),
                **extra_audit,
            )
        raise

    # Security validation: redact forbidden-column values the model may have
    # emitted despite the SQL guard (defense in depth). Scoped requests only —
    # the unscoped admin/open path has no rbac context.
    if rbac_ctx is not None:
        result.answer = rbac_ctx.strip_forbidden(result.answer)

    with db_session() as session:
        write_audit(
            session,
            slack_user_id=slack_user_id,
            employee_id=employee_id,
            role=role,
            question=question,
            answer=result.answer,
            tables_accessed=result.tables_accessed or None,
            schema_rag_ms=result.schema_rag_ms,
            agent_ms=result.agent_ms,
            total_ms=result.total_ms,
            prompt_tokens=result.prompt_tokens or None,
            completion_tokens=result.completion_tokens or None,
            total_tokens=result.total_tokens or None,
            **extra_audit,
        )

    return result
