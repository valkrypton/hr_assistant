"""Runtime selection — `get_runtime()` returns the configured AgentRuntime."""

from __future__ import annotations

from core.config import settings
from core.runtimes.base import AgentRunRequest, AgentRunResult, AgentRuntime

__all__ = ["AgentRunRequest", "AgentRunResult", "AgentRuntime", "get_runtime"]


def get_runtime() -> AgentRuntime:
    name = (settings.AGENT_RUNTIME or "legacy").lower()
    if name == "legacy":
        from core.runtimes.legacy import LegacyRuntime

        return LegacyRuntime()
    if name == "langgraph":
        from core.runtimes.langgraph.runtime import LangGraphRuntime

        return LangGraphRuntime()
    raise ValueError(
        f"Unknown AGENT_RUNTIME: {settings.AGENT_RUNTIME!r} (expected legacy|langgraph)"
    )
