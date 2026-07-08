"""
Tests for the core.policies layer (HRASSISTAN — PR2 policy hardening).

Covers:
  - can(role, action) matrix for all four roles across known + unknown actions
  - scope_for(role) tier derivation for all four roles
  - can()/scope_for() fail closed for unknown/None subjects
  - can() accepts anything with a `.role` attribute (e.g. RBACContext), not
    just a bare Role
  - wildcard permission matching (trailing "*")

No database or LLM involved — all pure unit tests.
"""

import pytest

from core.policies.evaluator import can, scope_for
from core.policies.permissions import ROLE_PERMISSIONS
from core.rbac.context import RBACContext
from core.rbac.roles import Role

ALL_ROLES = [Role.CTO_CEO, Role.HR_MANAGER, Role.DEPT_HEAD, Role.TEAM_LEAD]

# role -> expected scope tier
EXPECTED_SCOPE = {
    Role.CTO_CEO: "company",
    Role.HR_MANAGER: "company",
    Role.DEPT_HEAD: "department",
    Role.TEAM_LEAD: "team",
}

# (role, action) -> expected can() result, for the actions named in the task.
EXPECTED_CAN = {
    (Role.CTO_CEO, "data.scope.company"): True,
    (Role.CTO_CEO, "data.scope.department"): False,
    (Role.CTO_CEO, "data.scope.team"): False,
    (Role.CTO_CEO, "sql.execute"): True,
    (Role.CTO_CEO, "some.unknown.action"): False,
    (Role.HR_MANAGER, "data.scope.company"): True,
    (Role.HR_MANAGER, "data.scope.department"): False,
    (Role.HR_MANAGER, "data.scope.team"): False,
    (Role.HR_MANAGER, "sql.execute"): True,
    (Role.HR_MANAGER, "some.unknown.action"): False,
    (Role.DEPT_HEAD, "data.scope.company"): False,
    (Role.DEPT_HEAD, "data.scope.department"): True,
    (Role.DEPT_HEAD, "data.scope.team"): False,
    (Role.DEPT_HEAD, "sql.execute"): True,
    (Role.DEPT_HEAD, "some.unknown.action"): False,
    (Role.TEAM_LEAD, "data.scope.company"): False,
    (Role.TEAM_LEAD, "data.scope.department"): False,
    (Role.TEAM_LEAD, "data.scope.team"): True,
    (Role.TEAM_LEAD, "sql.execute"): True,
    (Role.TEAM_LEAD, "some.unknown.action"): False,
}


class TestCanMatrix:
    @pytest.mark.parametrize("role,action", list(EXPECTED_CAN.keys()))
    def test_can_matches_expected(self, role, action):
        assert can(role, action) is EXPECTED_CAN[(role, action)]


class TestScopeForMatrix:
    @pytest.mark.parametrize("role", ALL_ROLES)
    def test_scope_for_matches_expected_tier(self, role):
        assert scope_for(role) == EXPECTED_SCOPE[role]


class TestUnknownSubject:
    def test_can_with_none_subject_is_false(self):
        assert can(None, "data.scope.company") is False

    def test_can_with_object_lacking_role_is_false(self):
        assert can(object(), "sql.execute") is False

    def test_can_with_object_role_attribute_none_is_false(self):
        class Anonymous:
            role = None

        assert can(Anonymous(), "sql.execute") is False

    def test_scope_for_none_subject_is_none_tier(self):
        assert scope_for(None) == "none"

    def test_scope_for_object_lacking_role_is_none_tier(self):
        assert scope_for(object()) == "none"


class TestSubjectWithRoleAttribute:
    """can()/scope_for() accept anything carrying a `.role`, not just a bare Role."""

    def test_can_accepts_rbac_context_superuser(self):
        assert can(RBACContext.superuser(), "data.scope.company") is True

    def test_can_accepts_rbac_context_dept_head(self):
        ctx = RBACContext(role=Role.DEPT_HEAD, department_id=3)
        assert can(ctx, "data.scope.department") is True
        assert can(ctx, "data.scope.company") is False

    def test_scope_for_accepts_rbac_context(self):
        ctx = RBACContext(role=Role.TEAM_LEAD, team_id=7)
        assert scope_for(ctx) == "team"


class TestWildcardPermissions:
    def test_wildcard_permission_grants_matching_prefix(self, monkeypatch):
        monkeypatch.setitem(ROLE_PERMISSIONS, Role.TEAM_LEAD, frozenset({"employee.*"}))
        assert can(Role.TEAM_LEAD, "employee.leave.read") is True

    def test_wildcard_permission_denies_non_matching_action(self, monkeypatch):
        monkeypatch.setitem(ROLE_PERMISSIONS, Role.TEAM_LEAD, frozenset({"employee.*"}))
        assert can(Role.TEAM_LEAD, "other.thing") is False
