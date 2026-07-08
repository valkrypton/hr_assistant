"""Tool registry — register tools and list the ones a context may use."""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.tools.base import Tool

if TYPE_CHECKING:
    from core.identity.context import AgentContext


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def all(self) -> list[Tool]:
        return list(self._tools.values())

    def for_context(self, ctx: AgentContext | None) -> list[Tool]:
        """The tools this context is authorized to call."""
        return [t for t in self._tools.values() if t.authorized(ctx)]


registry = ToolRegistry()
