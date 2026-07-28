"""HRUser is a Slack-ID mapping only — no role or scope columns."""

import pytest
import sqlalchemy
from sqlalchemy.orm import Session

from api.schemas.users import UserCreate
from api.services.user_service import register_user
from core.rbac.models import Base, HRUser


@pytest.fixture
def session():
    engine = sqlalchemy.create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def test_hruser_has_no_role_column():
    assert not hasattr(HRUser, "role")
    assert not hasattr(HRUser, "department_id")
    assert not hasattr(HRUser, "team_id")


def test_register_user_without_role(session):
    created = register_user(session, UserCreate(employee_id=42, slack_user_id="U123"))
    assert created.employee_id == 42
    assert created.slack_user_id == "U123"


def test_user_create_rejects_role_field():
    with pytest.raises(Exception):
        UserCreate(employee_id=42, slack_user_id="U123", role="hr_manager")
