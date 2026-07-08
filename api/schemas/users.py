from pydantic import BaseModel

from core.rbac.roles import Role


class UserCreate(BaseModel):
    employee_id: int
    role: Role
    slack_user_id: str
    department_id: int | None = None
    team_id: int | None = None


class UserResponse(BaseModel):
    id: int
    employee_id: int
    role: Role
    slack_user_id: str | None
    department_id: int | None
    team_id: int | None
    is_active: bool
