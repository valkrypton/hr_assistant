from typing import Optional

from pydantic import BaseModel

from core.rbac.roles import Role


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
