"""Legacy runtime — wraps the existing langchain create_sql_agent path.

Deliberately imports core.agent lazily inside each method so that test mocks
targeting `core.agent.query` / `core.agent.get_agent` still intercept, and so
importing the runtime package doesn't pull in langchain until it's actually
used. Deleted in PR7 once the LangGraph runtime is the default.
"""

from __future__ import annotations

from core.runtimes.base import AgentRunRequest, AgentRunResult


class LegacyRuntime:
    def run(self, request: AgentRunRequest) -> AgentRunResult:
        from core.agent import query

        r = query(
            request.question,
            rbac_ctx=request.rbac_ctx,
            conversation_history=request.conversation_history,
        )
        return AgentRunResult(
            answer=r.answer,
            tables_accessed=r.tables_accessed,
            schema_rag_ms=r.schema_rag_ms,
            agent_ms=r.agent_ms,
            total_ms=r.total_ms,
            prompt_tokens=r.prompt_tokens,
            completion_tokens=r.completion_tokens,
            total_tokens=r.total_tokens,
        )

    def warmup(self) -> None:
        from core.agent import get_agent

        get_agent()
