"""Adapt core Tools into langchain tools bound to a request's AgentContext.

The context is captured in a closure, never passed as a tool argument — so the
scope and permissions come from the resolved identity, not from anything the LLM
can set. Each langchain tool delegates to Tool.execute, which enforces the
tool's required permissions and returns guard/permission rejections as
"Error: ..." observations the agent can react to.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from langchain_core.tools import StructuredTool

if TYPE_CHECKING:
    from core.identity.context import AgentContext
    from core.tools.base import Tool


def to_langchain_tools(ctx: AgentContext | None, tools: list[Tool]) -> list[StructuredTool]:
    lc_tools: list[StructuredTool] = []
    for tool in tools:

        def _make(bound_tool: Tool):
            def _call(**kwargs) -> str:
                return bound_tool.execute(ctx, **kwargs)

            return StructuredTool.from_function(
                func=_call,
                name=bound_tool.name,
                description=bound_tool.description,
                args_schema=bound_tool.input_model,
            )

        lc_tools.append(_make(tool))
    return lc_tools
