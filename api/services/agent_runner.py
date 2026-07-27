"""Default AgentRunner for query_service — delegates to core.agent.query,
resolved through the package at call time rather than bound at this module's
own import time.

Lives in api/services/, not core/: AgentRunner is an api-layer abstraction
(core/agent has no dependency on api/) — see api/services/interfaces.py.

Tests patch `core.agent.query` directly (e.g. tests/test_e2e.py's
module-scoped mock_query fixture). A direct `from core.agent import query as
agent_query` here would freeze whatever core.agent.query was the first time
this module got imported — which can happen before that patch is applied,
permanently missing it for the rest of the test process regardless of import
order elsewhere. Looking it up on `_pkg` inside the function body avoids that.
"""

from core import agent as _pkg


def default_agent_runner(query: str, rbac_ctx=None):
    return _pkg.query(query, rbac_ctx=rbac_ctx)
