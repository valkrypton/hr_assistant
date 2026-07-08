"""Tool layer — business capabilities the agent can call.

Each Tool carries its own required permissions and enforces them in the tool
layer (never via the prompt). The registry exposes only the tools a given
context is authorized to use.
"""

from core.tools.base import Tool
from core.tools.joiners import JOINERS_SUMMARY
from core.tools.leave import LEAVE_LOOKUP
from core.tools.registry import registry
from core.tools.sql import QUERY_ERP_SQL
from core.tools.team import TEAM_ROSTER

for _tool in (QUERY_ERP_SQL, TEAM_ROSTER, LEAVE_LOOKUP, JOINERS_SUMMARY):
    registry.register(_tool)

__all__ = [
    "JOINERS_SUMMARY",
    "LEAVE_LOOKUP",
    "QUERY_ERP_SQL",
    "TEAM_ROSTER",
    "Tool",
    "registry",
]
