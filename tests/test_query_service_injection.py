"""#4 DIP — verify resolve_scope/run_agent accept injected collaborators
without touching the real agent or DB."""

from core.agent import AgentQueryResult
from core.rbac.context import RBACContext
from core.rbac.roles import Role


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

    ctx = RBACContext.superuser()
    result = run_agent("how many staff?", ctx, agent=fake_agent)

    assert result.answer == "canned"
    assert captured["query"] == "how many staff?"
    assert captured["ctx"] is ctx


def test_resolve_scope_uses_injected_repo():
    from api.schemas.query import QueryRequest
    from api.services.query_service import resolve_scope

    class FakeUser:
        role = Role.CTO_CEO.value
        employee_id = 1
        department_id = None
        team_id = None

    class FakeRepo:
        @staticmethod
        def get_by_slack_user_id(session, slack_user_id):
            return FakeUser()

    body = QueryRequest(query="hi", slack_user_id="U123")
    ctx = resolve_scope(body, repo=FakeRepo())

    assert ctx is not None
    assert ctx.role == Role.CTO_CEO
