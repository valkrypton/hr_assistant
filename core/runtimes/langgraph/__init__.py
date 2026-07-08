"""LangGraph agent runtime — isolates all langchain/langgraph code.

Nothing outside core/runtimes/ imports this package; the rest of the app talks
to it only through the AgentRuntime interface (core.runtimes.get_runtime).
"""

from core.runtimes.langgraph.runtime import LangGraphRuntime

__all__ = ["LangGraphRuntime"]
