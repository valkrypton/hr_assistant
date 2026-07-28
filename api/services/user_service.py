"""Business logic for the /users routes — list/register/deregister HRUser."""

import structlog
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from api.schemas.users import UserCreate, UserResponse
from core.rbac.models import HRUser

logger = structlog.get_logger(__name__)


def list_users(session: Session) -> list[UserResponse]:
    return [
        UserResponse.model_validate(u)
        for u in session.query(HRUser).filter_by(is_active=True).all()
    ]


def register_user(session: Session, body: UserCreate) -> UserResponse:
    user = HRUser(
        employee_id=body.employee_id,
        slack_user_id=body.slack_user_id,
    )
    session.add(user)
    try:
        session.commit()
        session.refresh(user)
    except IntegrityError as exc:
        session.rollback()
        logger.warning("user_registration_conflict", error=str(exc))
        raise HTTPException(
            status_code=409, detail="A user with this Slack user ID already exists."
        ) from exc
    return UserResponse.model_validate(user)


def deregister_user(session: Session, user_id: int) -> None:
    user = session.get(HRUser, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")
    user.is_active = False
    session.commit()
