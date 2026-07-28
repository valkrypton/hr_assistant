"""ScopePolicy / RBACContext — two access levels, no roles."""

import pytest

from core.rbac.access import AccessLevel
from core.rbac.context import RBACContext
from core.rbac.erp_identity import ErpIdentity
from core.rbac.policy import ScopePolicy


def test_unrestricted_is_unrestricted():
    assert ScopePolicy(access_level=AccessLevel.UNRESTRICTED).is_unrestricted is True


def test_self_is_restricted():
    assert ScopePolicy(access_level=AccessLevel.SELF, person_id=7).is_unrestricted is False


def test_policy_is_frozen():
    policy = ScopePolicy(access_level=AccessLevel.SELF, person_id=7)
    with pytest.raises(Exception):
        policy.person_id = 8


def test_for_identity_carries_person_id():
    identity = ErpIdentity(person_id=42, auth_user_id=500, group_ids=frozenset({12}))
    ctx = RBACContext.for_identity(identity, AccessLevel.UNRESTRICTED)
    assert ctx.person_id == 42
    assert ctx.is_unrestricted is True


def test_for_identity_self_level():
    identity = ErpIdentity(person_id=42, auth_user_id=500, group_ids=frozenset())
    ctx = RBACContext.for_identity(identity, AccessLevel.SELF)
    assert ctx.is_unrestricted is False
    assert ctx.person_id == 42


def test_unrestricted_helper():
    assert RBACContext.unrestricted().is_unrestricted is True


def test_scope_hint_mentions_own_records_for_self():
    ctx = RBACContext(access_level=AccessLevel.SELF, person_id=42)
    assert "own records" in ctx.scope_hint().lower()


def test_scope_hint_mentions_company_wide_for_unrestricted():
    assert "company-wide" in RBACContext.unrestricted().scope_hint().lower()


def test_strip_forbidden_still_redacts():
    ctx = RBACContext.unrestricted()
    assert "REDACTED" in ctx.strip_forbidden("salary: 100000")
