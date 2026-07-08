"""Runtime-agnostic request/result types and the AgentRuntime protocol.

The rest of the application talks to an agent only through this interface, so
the concrete framework (today langchain's create_sql_agent, next LangGraph)
stays isolated in a runtime implementation. Nothing here imports langchain.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass
class AgentRunRequest:
    question: str
    rbac_ctx: object | None = None  # core.rbac.context.RBACContext | None
    conversation_history: list[dict] | None = None


@dataclass
class AgentRunResult:
    answer: str
    tables_accessed: str = ""  # comma-separated, may be empty
    schema_rag_ms: int = 0
    agent_ms: int = 0
    total_ms: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    # Observability superset — populated by newer runtimes / PR9. Empty under
    # the legacy runtime.
    sql_statements: list[str] = field(default_factory=list)
    tools_used: list[str] = field(default_factory=list)
    model_name: str = ""
    rows_returned: int = 0


@runtime_checkable
class AgentRuntime(Protocol):
    def run(self, request: AgentRunRequest) -> AgentRunResult: ...

    def warmup(self) -> None: ...
