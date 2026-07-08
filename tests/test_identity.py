"""
Tests for the identity layer (HRASSISTAN — PR3 identity resolver + AgentContext).

Covers:
  - lookup_hr_user(): active-user lookup, unknown slack id, inactive user
  - resolve(): happy path AgentContext construction, UserNotRegistered,
    MissingRole
  - AgentContext.for_rbac() / for_user(): permissions materialization,
    request_id uniqueness, metadata isolation

Uses an in-memory SQLite app DB (mirrors tests/test_rbac.py's
TestCountRecentQueries pattern) — no live database or LLM involved.
"""

import pytest
import sqlalchemy
from sqlalchemy.orm import Session

from core.errors import MissingRole, UserNotRegistered
from core.identity.context import AgentContext
from core.identity.resolver import lookup_hr_user, resolve
from core.policies import permissions_for
from core.rbac.context import RBACContext
from core.rbac.models import Base, HRUser
from core.rbac.roles import Role

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def engine():
    eng = sqlalchemy.create_engine(
        "sqlite:///:memory:",
        poolclass=sqlalchemy.pool.StaticPool,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(eng)
    return eng


@pytest.fixture()
def session(engine):
    with Session(engine) as s:
        yield s


def add_user(session, **kwargs):
    defaults = {
        "employee_id": 1,
        "role": Role.CTO_CEO.value,
        "slack_user_id": "U_DEFAULT",
        "is_active": True,
    }
    defaults.update(kwargs)
    user = HRUser(**defaults)
    session.add(user)
    session.commit()
    return user


# ---------------------------------------------------------------------------
# lookup_hr_user()
# ---------------------------------------------------------------------------


class TestLookupHrUser:
    def test_returns_active_user(self, session):
        add_user(session, employee_id=10, role=Role.HR_MANAGER.value, slack_user_id="U_ACTIVE")
        found = lookup_hr_user(session, "U_ACTIVE")
        assert found is not None
        assert found.employee_id == 10
        assert found.role == Role.HR_MANAGER.value

    def test_returns_none_for_unknown_slack_id(self, session):
        add_user(session, slack_user_id="U_KNOWN")
        assert lookup_hr_user(session, "U_UNKNOWN") is None

    def test_returns_none_for_inactive_user(self, session):
        add_user(session, slack_user_id="U_INACTIVE", is_active=False)
        assert lookup_hr_user(session, "U_INACTIVE") is None


# ---------------------------------------------------------------------------
# resolve() — happy path
# ---------------------------------------------------------------------------


class TestResolveHappyPath:
    def test_cto_ceo_context_matches_user(self, session):
        add_user(
            session,
            employee_id=42,
            role=Role.CTO_CEO.value,
            slack_user_id="U_CTO",
        )
        ctx = resolve(session, "U_CTO")
        assert isinstance(ctx, AgentContext)
        assert ctx.role == Role.CTO_CEO
        assert ctx.employee_id == 42
        assert ctx.slack_user_id == "U_CTO"
        assert ctx.permissions == permissions_for(Role.CTO_CEO)

    def test_team_lead_context_matches_user(self, session):
        add_user(
            session,
            employee_id=7,
            role=Role.TEAM_LEAD.value,
            slack_user_id="U_TL",
            team_id=99,
        )
        ctx = resolve(session, "U_TL")
        assert ctx.role == Role.TEAM_LEAD
        assert ctx.employee_id == 7
        assert ctx.slack_user_id == "U_TL"
        assert ctx.permissions == permissions_for(Role.TEAM_LEAD)
        assert ctx.rbac.team_id == 99


# ---------------------------------------------------------------------------
# resolve() — error paths
# ---------------------------------------------------------------------------


class TestResolveErrors:
    def test_unknown_slack_id_raises_user_not_registered(self, session):
        with pytest.raises(UserNotRegistered):
            resolve(session, "U_GHOST")

    def test_empty_role_raises_missing_role(self, session):
        add_user(session, slack_user_id="U_NOROLE", role="")
        with pytest.raises(MissingRole):
            resolve(session, "U_NOROLE")


# ---------------------------------------------------------------------------
# AgentContext — for_rbac / request_id / metadata
# ---------------------------------------------------------------------------


class TestAgentContextForRbac:
    def test_superuser_has_company_scope_permissions(self):
        ctx = AgentContext.for_rbac(RBACContext.superuser())
        assert ctx.permissions == permissions_for(Role.CTO_CEO)
        assert "data.scope.company" in ctx.permissions

    def test_request_id_is_32_char_hex(self):
        ctx = AgentContext.for_rbac(RBACContext.superuser())
        assert len(ctx.request_id) == 32
        int(ctx.request_id, 16)  # raises ValueError if not hex

    def test_request_id_differs_between_instances(self):
        ctx1 = AgentContext.for_rbac(RBACContext.superuser())
        ctx2 = AgentContext.for_rbac(RBACContext.superuser())
        assert ctx1.request_id != ctx2.request_id

    def test_metadata_defaults_to_independent_empty_dict(self):
        ctx1 = AgentContext.for_rbac(RBACContext.superuser())
        ctx2 = AgentContext.for_rbac(RBACContext.superuser())
        assert ctx1.metadata == {}
        assert ctx2.metadata == {}
        ctx1.metadata["foo"] = "bar"
        assert ctx2.metadata == {}
