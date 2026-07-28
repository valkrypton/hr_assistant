"""#4 DIP — verify resolve_scope_from_session/run_agent accept injected
collaborators without touching the real agent or DB."""

import pytest
from fastapi import HTTPException

from core.agent import AgentQueryResult
from core.rbac.access import AccessLevel
from core.rbac.context import RBACContext
from core.rbac.erp_identity import ErpIdentity


def test_run_agent_uses_injected_agent():
    captured = {}

    def fake_agent(query, rbac_ctx=None):
        captured["query"] = query
        captured["ctx"] = rbac_ctx
        return AgentQueryResult(
            answer="canned",
            tables_accessed="",
            schema_rag_ms=0,
            agent_ms=0,
            total_ms=0,
        )

    from api.services.query_service import run_agent

    ctx = RBACContext.unrestricted()
    result = run_agent("how many staff?", ctx, agent=fake_agent)

    assert result.answer == "canned"
    assert captured["query"] == "how many staff?"
    assert captured["ctx"] is ctx


def test_resolve_scope_from_session_uses_injected_resolver():
    from api.services.query_service import resolve_scope_from_session

    def fake_resolve_ctx(person_id):
        identity = ErpIdentity(person_id=person_id, auth_user_id=999, group_ids=frozenset({12}))
        return RBACContext.for_identity(identity, AccessLevel.UNRESTRICTED)

    ctx = resolve_scope_from_session(1, resolve_ctx=fake_resolve_ctx)

    assert ctx.is_unrestricted is True
    assert ctx.person_id == 1


def test_resolve_scope_from_session_denies_when_resolver_returns_none():
    """No active ERP identity for this person_id — must be denied (403),
    never silently unrestricted."""
    from api.services.query_service import resolve_scope_from_session

    def fake_resolve_ctx(person_id):
        return None

    with pytest.raises(HTTPException) as exc_info:
        resolve_scope_from_session(1, resolve_ctx=fake_resolve_ctx)
    assert exc_info.value.status_code == 403
