"""#4 DIP — verify resolve_scope/run_agent accept injected collaborators
without touching the real agent or DB."""

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


def test_resolve_scope_uses_injected_repo_and_resolver():
    from api.schemas.query import QueryRequest
    from api.services.query_service import resolve_scope

    class FakeUser:
        employee_id = 1

    class FakeRepo:
        @staticmethod
        def get_by_slack_user_id(session, slack_user_id):
            return FakeUser()

    def fake_resolve_ctx(person_id):
        identity = ErpIdentity(person_id=person_id, auth_user_id=999, group_ids=frozenset({12}))
        return RBACContext.for_identity(identity, AccessLevel.UNRESTRICTED)

    body = QueryRequest(query="hi", slack_user_id="U123")
    ctx = resolve_scope(body, repo=FakeRepo(), resolve_ctx=fake_resolve_ctx)

    assert ctx is not None
    assert ctx.is_unrestricted is True
    assert ctx.person_id == 1
