from pydantic import BaseModel, ConfigDict


class UserCreate(BaseModel):
    # extra="forbid" so a stale client still sending `role`/`department_id`
    # gets a 422 rather than having the field silently ignored.
    model_config = ConfigDict(extra="forbid")

    employee_id: int
    slack_user_id: str


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    employee_id: int
    slack_user_id: str | None
    is_active: bool
