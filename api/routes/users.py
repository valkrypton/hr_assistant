from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.deps import app_engine
from core.rbac.models import HRUser
from core.rbac.roles import Role

router = APIRouter(prefix="/users")


class UserCreate(BaseModel):
    employee_id: int
    role: Role
    slack_user_id: str
    department_id: Optional[int] = None
    team_id: Optional[int] = None


class UserResponse(BaseModel):
    id: int
    employee_id: int
    role: Role
    slack_user_id: Optional[str]
    department_id: Optional[int]
    team_id: Optional[int]
    is_active: bool


@router.get("", response_model=list[UserResponse])
def list_users():
    """List all active HR agent users."""
    with Session(app_engine()) as session:
        return [
            UserResponse(
                id=u.id,
                employee_id=u.employee_id,
                role=u.role,
                slack_user_id=u.slack_user_id,
                department_id=u.department_id,
                team_id=u.team_id,
                is_active=u.is_active,
            )
            for u in session.query(HRUser).filter_by(is_active=True).all()
        ]


@router.post("", response_model=UserResponse, status_code=201)
def register_user(body: UserCreate):
    """Register an employee as an HR agent user with a given role."""
    with Session(app_engine()) as session:
        user = HRUser(
            employee_id=body.employee_id,
            role=body.role.value,
            slack_user_id=body.slack_user_id,
            department_id=body.department_id,
            team_id=body.team_id,
        )
        session.add(user)
        try:
            session.commit()
            session.refresh(user)
        except Exception as exc:
            session.rollback()
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return UserResponse(
            id=user.id,
            employee_id=user.employee_id,
            role=user.role,
            slack_user_id=user.slack_user_id,
            department_id=user.department_id,
            team_id=user.team_id,
            is_active=user.is_active,
        )


@router.delete("/{user_id}", status_code=204)
def deregister_user(user_id: int):
    """Deactivate a user (soft delete)."""
    with Session(app_engine()) as session:
        user = session.get(HRUser, user_id)
        if not user:
            raise HTTPException(status_code=404, detail="User not found.")
        user.is_active = False
        session.commit()
