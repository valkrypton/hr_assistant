"""Query execution — retry/backoff, latency timing, token accounting, table
extraction, and forbidden-column redaction around the built agent. Split out of
the old core/agent.py (SRP: execution vs construction)."""

import time
from dataclasses import dataclass

import structlog

from core import agent as _pkg
from core.agent.enrichment import build_enriched_input, load_schema_block
from core.agent.factory import reset_shared_agent

logger = structlog.get_logger(__name__)


@dataclass
class AgentQueryResult:
    answer: str
    tables_accessed: str  # comma-separated, may be empty string
    schema_rag_ms: int  # schema load time (file read, not RAG)
    agent_ms: int  # LLM + SQL execution time
    total_ms: int  # full round-trip
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


def query(
    user_input: str,
    rbac_ctx=None,
    conversation_history: list[dict] | None = None,
) -> AgentQueryResult:
    """
    Run a natural-language HR query.

    Args:
        user_input: The question to answer.
        rbac_ctx: Optional RBAC context scoping the response.
        conversation_history: Optional list of prior turns in the format
            [{"role": "user"|"assistant", "content": "..."}].
            Injected before the current question so the agent can resolve
            follow-up references (e.g. "who are the newest ones?").

    Returns a AgentQueryResult with answer, tables_accessed, and latency breakdown.
    """
    t_total_start = time.monotonic()

    # Step 1: Load full schema — small enough (~3k tokens) to inject entirely.
    # No chunking/RAG needed; the full schema is injected directly, avoiding
    # lossy retrieval. Cached after the first read — schema.md doesn't change
    # while the process is running.
    t_rag_start = time.monotonic()
    schema_block = load_schema_block()
    schema_rag_ms = int((time.monotonic() - t_rag_start) * 1000)

    # Step 2: Build enriched message
    enriched_input = build_enriched_input(user_input, rbac_ctx, schema_block, conversation_history)

    # Step 3: Run agent with retry — up to 2 retries on transient failures.
    t_agent_start = time.monotonic()
    last_exc: Exception | None = None
    result = None
    prompt_tokens = completion_tokens = total_tokens = 0

    for attempt in range(3):
        if attempt > 0:
            wait = 2**attempt  # 2s, 4s
            logger.warning(
                "agent_attempt_failed", attempt=attempt, wait_seconds=wait, error=str(last_exc)
            )
            time.sleep(wait)
        try:
            with _pkg.get_openai_callback() as cb:
                result = _pkg.get_agent(rbac_ctx).invoke({"input": enriched_input})
            prompt_tokens = cb.prompt_tokens
            completion_tokens = cb.completion_tokens
            total_tokens = cb.total_tokens
            last_exc = None
            break
        except Exception as exc:
            last_exc = exc
            # Reset the shared cached agent on failure so next attempt gets a
            # fresh one — but only when the failing agent WAS the shared one.
            # Restricted-role agents are built per-request and never cached
            # (see get_agent), so resetting _agent for them would be a no-op
            # at best and would needlessly discard a working shared agent.
            if rbac_ctx is None or rbac_ctx.is_unrestricted:
                reset_shared_agent()

    if result is None:
        # All retries exhausted — return a user-friendly message, don't raise.
        logger.error("agent_failed_after_retries", attempts=3, error=str(last_exc))
        total_ms = int((time.monotonic() - t_total_start) * 1000)
        return AgentQueryResult(
            answer="Sorry, I wasn't able to process your request right now. Please try again in a moment.",
            tables_accessed="",
            schema_rag_ms=schema_rag_ms,
            agent_ms=int((time.monotonic() - t_agent_start) * 1000),
            total_ms=total_ms,
        )

    agent_ms = int((time.monotonic() - t_agent_start) * 1000)
    answer = result.get("output", str(result))
    tables_accessed = _pkg._extract_tables(result.get("intermediate_steps", []))

    # Step 4: Redact forbidden columns
    if rbac_ctx is not None:
        answer = rbac_ctx.strip_forbidden(answer)

    total_ms = int((time.monotonic() - t_total_start) * 1000)

    logger.info(
        "query_completed",
        total_ms=total_ms,
        agent_ms=agent_ms,
        schema_rag_ms=schema_rag_ms,
        tokens=total_tokens,
        tables=tables_accessed or "none",
    )

    return AgentQueryResult(
        answer=answer,
        tables_accessed=tables_accessed,
        schema_rag_ms=schema_rag_ms,
        agent_ms=agent_ms,
        total_ms=total_ms,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
    )
