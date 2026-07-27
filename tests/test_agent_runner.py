"""Coverage for api/services/agent_runner.py — default_agent_runner delegates
to core.agent.query, resolved at call time (not bound at this module's own
import time) so patches on core.agent.query still take effect regardless of
when this module was first imported."""

from unittest.mock import patch

from api.services.agent_runner import default_agent_runner


def test_delegates_to_core_agent_query():
    with patch("core.agent.query", return_value="canned") as mock_query:
        result = default_agent_runner("how many staff?", rbac_ctx=None)

    mock_query.assert_called_once_with("how many staff?", rbac_ctx=None)
    assert result == "canned"
