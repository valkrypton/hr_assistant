"""
HR Agent - core layer.

This module has NO dependency on the API layer.  It can be imported and used
standalone (scripts, tests, notebooks) without starting a web server.
"""

import copy
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import structlog
from langchain_community.agent_toolkits import create_sql_agent
from langchain_community.callbacks import get_openai_callback
from langchain_community.utilities import SQLDatabase

from core.agent_prompts import HR_RECORDS_NOTE, build_prefix
from core.agent_sql_extraction import extract_tables as _extract_tables
from core.config import DEFAULT_ENGINE_ARGS, settings
from core.providers.factory import get_llm
from core.rbac.sql_guard import (
    assert_person_free_tables_have_no_person_fk,
    assert_tables_classified,
)

logger = structlog.get_logger(__name__)


@dataclass
class QueryResult:
    answer: str
    tables_accessed: str  # comma-separated, may be empty string
    schema_rag_ms: int  # schema load time (file read, not RAG)
    agent_ms: int  # LLM + SQL execution time
    total_ms: int  # full round-trip
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


# ---------------------------------------------------------------------------
# Agent construction
# ---------------------------------------------------------------------------


def _get_included_tables() -> list[str]:
    tables = settings.included_tables
    if not tables:
        raise ValueError(
            "INCLUDED_TABLES must be set in .env. "
            "List only the tables the agent needs (e.g. person,department,leave_record)."
        )
    assert_tables_classified(tables)
    return tables


# ---------------------------------------------------------------------------
# ERP SQLDatabase — built once per (url, tables), not once per request.
#
# from_uri() does create_engine() + a full MetaData.reflect() over every
# included table (lazy_table_reflection defaults to false). Building it per
# restricted-role request meant every dept_head/team_lead query paid that
# cost and leaked an undisposed engine. Cached here and shallow-copied per
# build so each caller gets its own `.run` (see _build_agent) while sharing
# the underlying engine/pool and reflected metadata. Keyed on (url, tables)
# rather than no-args so tests pointing at a throwaway sqlite file per test
# don't collide with each other or with the real ERP connection.
# ---------------------------------------------------------------------------


@lru_cache(maxsize=8)
def _erp_db(database_url: str, included_tables: tuple[str, ...]) -> SQLDatabase:
    return SQLDatabase.from_uri(
        database_url,
        include_tables=list(included_tables),
        sample_rows_in_table_info=0,
        engine_args={
            **DEFAULT_ENGINE_ARGS,
            "pool_size": settings.ERP_POOL_SIZE,
            "max_overflow": settings.ERP_MAX_OVERFLOW,
        },
    )


# Cache for _hr_records_available — deliberately not @lru_cache. A transient
# ERP error on the first probe must not pin a permanent False (and thus
# permanently inject _HR_RECORDS_NOTE) for the process lifetime; only a
# confirmed True is worth remembering.
_hr_records_cache: dict[tuple[str, tuple[str, ...]], bool] = {}


def _hr_records_available(database_url: str, included_tables: tuple[str, ...]) -> bool:
    """Whether the hr_records table exists in the ERP schema — a fixed fact
    about the database, not the requester's role, so probed once via the
    unscoped connection rather than per built agent."""
    key = (database_url, included_tables)
    if _hr_records_cache.get(key):
        return True
    try:
        _erp_db(database_url, included_tables).run("SELECT 1 FROM hr_records LIMIT 1")
        _hr_records_cache[key] = True
        return True
    except Exception:
        return False


def _build_agent(rbac_ctx=None):
    """
    Build the SQL agent with the given RBAC context baked into the system prefix.

    When rbac_ctx is None the agent is built without scope restrictions (used
    for the shared unauthenticated agent and for superuser access).  When a
    context is provided, the role and scope are embedded in the prefix so the
    LLM treats them as immutable system rules rather than advisory hints.

    db.run is always wrapped to pass every SQL statement through
    sql_guard.rewrite_sql before execution.  This blocks non-SELECT
    statements for all roles (including CTO/CEO and unauthenticated) and
    additionally injects scope predicates for restricted roles, making
    both protections immune to prompt injection.
    """
    llm = get_llm()
    included = tuple(_get_included_tables())
    db = copy.copy(_erp_db(settings.DATABASE_URL, included))
    # Defense-in-depth against a person-bearing table wrongly classified as
    # person-free — assert_tables_classified (above) can't catch this since
    # a mis-listed table is still, by definition, present in one of the
    # three sets.
    assert_person_free_tables_have_no_person_fk(db._metadata, list(included))

    # Imported per-call (not hoisted to module level) so tests can patch
    # core.rbac.sql_guard.rewrite_sql before this runs — see
    # tests/test_agent_scoped_run.py._build_scoped_db for why a module-level
    # alias would not be patchable the same way.
    from core.rbac.sql_guard import rewrite_sql as _rewrite

    _original_run = db.run

    def _scoped_run(command, fetch="all", **kwargs):
        try:
            command = _rewrite(command, rbac_ctx)
        except ValueError as exc:
            # Surface guard rejections (forbidden column, wildcard, non-SELECT,
            # unclassified table) to the agent as a tool observation so it can
            # rewrite the SQL.  LangChain's run_no_throw only catches
            # SQLAlchemyError, so a raised ValueError would abort the whole run.
            return f"Error: {exc}"
        return _original_run(command, fetch=fetch, **kwargs)

    db.run = _scoped_run

    hr_records_note = (
        "" if _hr_records_available(settings.DATABASE_URL, included) else HR_RECORDS_NOTE
    )
    prefix = build_prefix(rbac_ctx, hr_records_note)

    return create_sql_agent(
        llm=llm,
        db=db,
        verbose=settings.DEBUG,
        prefix=prefix,
        max_iterations=10,
        agent_type="tool-calling",
        agent_executor_kwargs={
            "handle_parsing_errors": True,
            "return_intermediate_steps": True,
        },
    )


# Shared agent for unauthenticated / superuser requests (built lazily).
_agent = None


def get_agent(rbac_ctx=None):
    """
    Return an agent appropriate for the given RBAC context.

    - No context / unrestricted → reuse the shared cached agent.
    - Restricted role (dept_head / team_lead) → build a fresh agent with the
      scope baked into the system prefix. These are not cached because each
      user has a different scope.
    """
    global _agent
    if rbac_ctx is None or rbac_ctx.is_unrestricted:
        if _agent is None:
            _agent = _build_agent(rbac_ctx)
        return _agent
    # Restricted: build per-request so the prefix carries the exact scope.
    return _build_agent(rbac_ctx)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def _load_schema_block() -> str:
    schema_path = Path(__file__).parent / "context" / "schema.md"
    return schema_path.read_text() if schema_path.exists() else ""


# ---------------------------------------------------------------------------
# Query — retrieve schema context at call time, inject into user message
# ---------------------------------------------------------------------------


def query(
    user_input: str,
    rbac_ctx=None,
    conversation_history: list[dict] | None = None,
) -> QueryResult:
    """
    Run a natural-language HR query.

    Args:
        user_input: The question to answer.
        rbac_ctx: Optional RBAC context scoping the response.
        conversation_history: Optional list of prior turns in the format
            [{"role": "user"|"assistant", "content": "..."}].
            Injected before the current question so the agent can resolve
            follow-up references (e.g. "who are the newest ones?").

    Returns a QueryResult with answer, tables_accessed, and latency breakdown.
    """
    t_total_start = time.monotonic()

    # Step 1: Load full schema — small enough (~3k tokens) to inject entirely.
    # No chunking/RAG needed; the full schema is injected directly, avoiding
    # lossy retrieval. Cached after the first read — schema.md doesn't change
    # while the process is running.
    t_rag_start = time.monotonic()
    schema_block = _load_schema_block()
    schema_rag_ms = int((time.monotonic() - t_rag_start) * 1000)

    # Step 2: Build enriched message
    parts = []
    if rbac_ctx is not None:
        parts.append(f"[Access control rules for this request]\n{rbac_ctx.scope_prompt()}")
    if schema_block:
        parts.append(f"[Full schema context]\n\n{schema_block}")
    if conversation_history:
        history_lines = []
        for turn in conversation_history:
            role = "User" if turn["role"] == "user" else "Assistant"
            history_lines.append(f"{role}: {turn['content']}")
        parts.append(
            "[Conversation history — earlier turns in this thread]\n" + "\n".join(history_lines)
        )
    parts.append(f"[Question]\n{user_input}")
    enriched_input = "\n\n".join(parts)

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
            with get_openai_callback() as cb:
                result = get_agent(rbac_ctx).invoke({"input": enriched_input})
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
                global _agent
                _agent = None

    if result is None:
        # All retries exhausted — return a user-friendly message, don't raise.
        logger.error("agent_failed_after_retries", attempts=3, error=str(last_exc))
        total_ms = int((time.monotonic() - t_total_start) * 1000)
        return QueryResult(
            answer="Sorry, I wasn't able to process your request right now. Please try again in a moment.",
            tables_accessed="",
            schema_rag_ms=schema_rag_ms,
            agent_ms=int((time.monotonic() - t_agent_start) * 1000),
            total_ms=total_ms,
        )

    agent_ms = int((time.monotonic() - t_agent_start) * 1000)
    answer = result.get("output", str(result))
    tables_accessed = _extract_tables(result.get("intermediate_steps", []))

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

    return QueryResult(
        answer=answer,
        tables_accessed=tables_accessed,
        schema_rag_ms=schema_rag_ms,
        agent_ms=agent_ms,
        total_ms=total_ms,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
    )
