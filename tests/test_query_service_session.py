"""resolve_scope_from_session — RBAC resolution for an already-authenticated
session-cookie user. No HRUser/slack_user_id lookup involved; identity is
already proven by the cookie itself (see api/deps.py:get_session_user)."""

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import SQLAlchemyError

from api.services.query_service import resolve_scope_from_session
from core.rbac.access import AccessLevel
from core.rbac.context import RBACContext


def test_returns_context_on_success():
    def fake_resolver(person_id):
        return RBACContext(access_level=AccessLevel.SELF, person_id=person_id)

    ctx = resolve_scope_from_session(42, resolve_ctx=fake_resolver)
    assert ctx.person_id == 42
    assert ctx.is_unrestricted is False


def test_none_from_resolver_raises_403():
    def fake_resolver(person_id):
        return None

    with pytest.raises(HTTPException) as exc_info:
        resolve_scope_from_session(42, resolve_ctx=fake_resolver)
    assert exc_info.value.status_code == 403


def test_sqlalchemy_error_raises_503():
    def fake_resolver(person_id):
        raise SQLAlchemyError("erp is down")

    with pytest.raises(HTTPException) as exc_info:
        resolve_scope_from_session(42, resolve_ctx=fake_resolver)
    assert exc_info.value.status_code == 503
