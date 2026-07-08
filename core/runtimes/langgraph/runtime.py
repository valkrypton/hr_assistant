"""LangGraph agent runtime.

Implements the AgentRuntime interface on langchain's create_agent (a LangGraph
graph). Per request it builds context-bound tools (scope comes from the resolved
identity, never LLM args), assembles the ported system prompt + enriched user
message, runs the graph with a bounded recursion limit and retry/backoff, and
returns a raw AgentRunResult — output redaction is the pipeline's job, so every
runtime redacts in exactly one place.
"""

from __future__ import annotations

import time
from pathlib import Path

import sqlalchemy
import structlog
from langchain.agents import create_agent
from langchain_core.messages import AIMessage, HumanMessage

from core.db import erp_engine
from core.identity.context import AgentContext
from core.providers.factory import get_llm
from core.runtimes.base import AgentRunRequest, AgentRunResult
from core.runtimes.langgraph.prompts import build_system_prompt, build_user_message
from core.runtimes.langgraph.tool_adapter import to_langchain_tools
from core.tools import registry
from core.tools.sql import SqlCollector

logger = structlog.get_logger(__name__)

# Bounds runaway tool loops. create_agent counts graph steps (~2 per
# model+tool cycle), so this is roughly the legacy agent's max_iterations=10.
_RECURSION_LIMIT = 25

_RETRY_ATTEMPTS = 3

_FAILURE_ANSWER = (
    "Sorry, I wasn't able to process your request right now. Please try again in a moment."
)

# Module-level caches — schema text and the hr_records probe don't change within
# a process, so pay their cost once instead of per request.
_schema_cache: str | None = None
_hr_records_cache: bool | None = None


def _schema_text() -> str:
    global _schema_cache
    if _schema_cache is None:
        path = Path(__file__).resolve().parents[2] / "context" / "schema.md"
        _schema_cache = path.read_text() if path.exists() else ""
    return _schema_cache


def _hr_records_available() -> bool:
    """Probe the ERP directly (not through the scoped SQL tool) so a restricted
    role doesn't misreport the table as present — fixes the legacy bug where the
    guard wrapper returned an 'Error:' string instead of raising."""
    global _hr_records_cache
    if _hr_records_cache is None:
        try:
            with erp_engine().connect() as conn:
                conn.execute(sqlalchemy.text("SELECT 1 FROM hr_records LIMIT 1"))
            _hr_records_cache = True
        except Exception:
            _hr_records_cache = False
    return _hr_records_cache


def _content_to_str(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                parts.append(block.get("text", ""))
            else:
                parts.append(str(block))
        return "".join(parts)
    return str(content)


def _final_answer(messages: list) -> str:
    for msg in reversed(messages):
        if isinstance(msg, AIMessage):
            text = _content_to_str(msg.content)
            if text:
                return text
    return ""


def _sum_tokens(messages: list) -> tuple[int, int, int]:
    prompt = completion = total = 0
    for msg in messages:
        usage = getattr(msg, "usage_metadata", None)
        if usage:
            prompt += usage.get("input_tokens", 0)
            completion += usage.get("output_tokens", 0)
            total += usage.get("total_tokens", 0)
    return prompt, completion, total


def _tools_used(messages: list) -> list[str]:
    used: list[str] = []
    for msg in messages:
        for call in getattr(msg, "tool_calls", None) or []:
            used.append(call["name"])
    return used


def _model_name(llm) -> str:
    return str(getattr(llm, "model_name", None) or getattr(llm, "model", None) or "")


class LangGraphRuntime:
    def warmup(self) -> None:
        _schema_text()
        _hr_records_available()

    def run(self, request: AgentRunRequest) -> AgentRunResult:
        t_total = time.monotonic()
        rbac_ctx = request.rbac_ctx

        # Build an AgentContext for the tool layer (permissions + a telemetry
        # collector). Unscoped requests (rbac_ctx=None) run without one.
        ctx = AgentContext.for_rbac(rbac_ctx) if rbac_ctx is not None else None
        collector: SqlCollector | None = None
        if ctx is not None:
            collector = SqlCollector()
            ctx.metadata["_sql_collector"] = collector

        t_rag = time.monotonic()
        schema_block = _schema_text()
        schema_rag_ms = int((time.monotonic() - t_rag) * 1000)

        system_prompt = build_system_prompt(rbac_ctx, _hr_records_available())
        user_message = build_user_message(
            request.question, rbac_ctx, schema_block, request.conversation_history
        )

        llm = get_llm()
        tools = to_langchain_tools(ctx, registry.for_context(ctx))
        agent = create_agent(model=llm, tools=tools, system_prompt=system_prompt)

        t_agent = time.monotonic()
        last_exc: Exception | None = None
        result = None
        for attempt in range(_RETRY_ATTEMPTS):
            if attempt > 0:
                wait = 2**attempt  # 2s, 4s
                logger.warning(
                    "langgraph_attempt_failed",
                    attempt=attempt,
                    wait_seconds=wait,
                    error=str(last_exc),
                )
                time.sleep(wait)
            try:
                result = agent.invoke(
                    {"messages": [HumanMessage(content=user_message)]},
                    config={"recursion_limit": _RECURSION_LIMIT},
                )
                last_exc = None
                break
            except Exception as exc:
                last_exc = exc

        if result is None:
            logger.error("langgraph_failed_after_retries", error=str(last_exc))
            return AgentRunResult(
                answer=_FAILURE_ANSWER,
                schema_rag_ms=schema_rag_ms,
                agent_ms=int((time.monotonic() - t_agent) * 1000),
                total_ms=int((time.monotonic() - t_total) * 1000),
                model_name=_model_name(llm),
            )

        agent_ms = int((time.monotonic() - t_agent) * 1000)
        messages = result.get("messages", [])
        answer = _final_answer(messages)
        prompt_tokens, completion_tokens, total_tokens = _sum_tokens(messages)
        tools_used = _tools_used(messages)
        total_ms = int((time.monotonic() - t_total) * 1000)

        sql_statements = list(collector.statements) if collector else []
        tables_accessed = ", ".join(sorted(collector.tables)) if collector else ""
        rows_returned = collector.rows_returned if collector else 0

        logger.info(
            "langgraph_query_completed",
            total_ms=total_ms,
            agent_ms=agent_ms,
            tokens=total_tokens,
            tools=tools_used or "none",
            tables=tables_accessed or "none",
        )

        return AgentRunResult(
            answer=answer,
            tables_accessed=tables_accessed,
            schema_rag_ms=schema_rag_ms,
            agent_ms=agent_ms,
            total_ms=total_ms,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            sql_statements=sql_statements,
            tools_used=tools_used,
            model_name=_model_name(llm),
            rows_returned=rows_returned,
        )
