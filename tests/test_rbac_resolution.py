"""Composition root — person_id to RBACContext."""

from core.rbac.access import AccessLevel
from core.rbac.erp_identity import ErpIdentity
from core.rbac.resolution import resolve_context


class _StubResolver:
    def __init__(self, identity):
        self._identity = identity

    def by_person_id(self, person_id):
        return self._identity


def test_hr_group_member_is_unrestricted():
    identity = ErpIdentity(person_id=42, auth_user_id=500, group_ids=frozenset({12}))
    ctx = resolve_context(42, resolver=_StubResolver(identity))
    assert ctx.is_unrestricted is True
    assert ctx.person_id == 42


def test_management_group_member_is_unrestricted():
    identity = ErpIdentity(person_id=42, auth_user_id=500, group_ids=frozenset({13}))
    ctx = resolve_context(42, resolver=_StubResolver(identity))
    assert ctx.is_unrestricted is True


def test_non_member_is_self_scoped():
    identity = ErpIdentity(person_id=42, auth_user_id=500, group_ids=frozenset({9, 10}))
    ctx = resolve_context(42, resolver=_StubResolver(identity))
    assert ctx.is_unrestricted is False
    assert ctx.person_id == 42


def test_unknown_person_returns_none():
    assert resolve_context(42, resolver=_StubResolver(None)) is None


def test_access_level_is_the_configured_pair(monkeypatch):
    from core.config import settings

    monkeypatch.setattr(settings, "HR_GROUP_ID", 77, raising=False)
    monkeypatch.setattr(settings, "MANAGEMENT_GROUP_ID", 78, raising=False)
    identity = ErpIdentity(person_id=1, auth_user_id=2, group_ids=frozenset({12}))
    ctx = resolve_context(1, resolver=_StubResolver(identity))
    assert ctx.access_level is AccessLevel.SELF
