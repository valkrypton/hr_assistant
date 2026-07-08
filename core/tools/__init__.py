"""Tool layer — business capabilities the agent can call.

Each Tool carries its own required permissions and enforces them in the tool
layer (never via the prompt). The registry exposes only the tools a given
context is authorized to use.
"""

from core.tools.base import Tool
from core.tools.registry import registry
from core.tools.sql import QUERY_ERP_SQL

registry.register(QUERY_ERP_SQL)

__all__ = ["QUERY_ERP_SQL", "Tool", "registry"]
