"""
Tests for the tool layer (HRASSISTAN — PR5 tool registry + Tool.execute).

Covers:
  - the global registry exposes QUERY_ERP_SQL and includes it for an
    unscoped (None) context
  - permission gating: for_context() excludes a tool whose required
    permission no role holds, and includes one requiring a permission the
    caller's role does hold; Tool.execute() returns a permission-denied
    string for the former WITHOUT invoking its fn
  - Tool.authorized(None) is always True (unscoped/admin path), even for a
    tool with required_permissions

No database or LLM involved. The permission-gating tests build their own
throwaway ToolRegistry() rather than mutating the module-level `registry`, to
avoid leaking a bogus tool into other tests that import it.
"""

from pydantic import BaseModel

from core.identity.context import AgentContext
from core.rbac.context import RBACContext
from core.rbac.roles import Role
from core.tools import QUERY_ERP_SQL, registry
from core.tools.base import Tool
from core.tools.registry import ToolRegistry


class _NoInput(BaseModel):
    pass


# ---------------------------------------------------------------------------
# Global registry
# ---------------------------------------------------------------------------


class TestGlobalRegistry:
    def test_get_returns_query_erp_sql(self):
        assert registry.get("query_erp_sql") is QUERY_ERP_SQL

    def test_for_context_none_includes_query_erp_sql(self):
        assert QUERY_ERP_SQL in registry.for_context(None)


# ---------------------------------------------------------------------------
# Permission gating — fresh registry, no global mutation
# ---------------------------------------------------------------------------


class TestPermissionGating:
    def _fn_raises_if_called(self, ctx, **kwargs) -> str:
        raise AssertionError("fn must not be called when the tool is unauthorized")

    def _make_registry(self):
        fresh = ToolRegistry()
        unauthorized_tool = Tool(
            name="throwaway_unauthorized",
            description="requires a permission no role holds",
            input_model=_NoInput,
            required_permissions=("nonexistent.permission",),
            fn=self._fn_raises_if_called,
        )
        authorized_tool = Tool(
            name="throwaway_authorized",
            description="requires sql.execute, which team_lead holds",
            input_model=_NoInput,
            required_permissions=("sql.execute",),
            fn=lambda ctx, **kwargs: "ok",
        )
        fresh.register(unauthorized_tool)
        fresh.register(authorized_tool)
        return fresh, unauthorized_tool, authorized_tool

    def test_for_context_excludes_unheld_permission_includes_held_one(self):
        fresh, unauthorized_tool, authorized_tool = self._make_registry()
        team_lead_ctx = AgentContext.for_rbac(RBACContext(role=Role.TEAM_LEAD, team_id=1))

        available = fresh.for_context(team_lead_ctx)

        assert unauthorized_tool not in available
        assert authorized_tool in available

    def test_execute_returns_permission_denied_without_calling_fn(self):
        fresh, unauthorized_tool, _ = self._make_registry()
        team_lead_ctx = AgentContext.for_rbac(RBACContext(role=Role.TEAM_LEAD, team_id=1))

        result = unauthorized_tool.execute(team_lead_ctx)

        assert isinstance(result, str)
        assert result.startswith("Error: permission denied")


# ---------------------------------------------------------------------------
# Tool.authorized(None) — unscoped/admin path
# ---------------------------------------------------------------------------


class TestAuthorizedNoneContext:
    def test_authorized_true_for_none_ctx_even_with_required_permissions(self):
        tool = Tool(
            name="throwaway_requires_perm",
            description="requires a permission, but None ctx bypasses the check",
            input_model=_NoInput,
            required_permissions=("nonexistent.permission",),
            fn=lambda ctx, **kwargs: "ok",
        )
        assert tool.authorized(None) is True
