# DEPRECATED — registers users for the old Slack-identity RBAC path, superseded
# by Google SSO login (api/routes/auth.py). Kept as-is; may be removed later.
from fastapi import APIRouter, Depends

from api.deps import DbDep, require_admin
from api.schemas.users import UserCreate, UserResponse
from api.services import user_service

router = APIRouter(prefix="/users", dependencies=[Depends(require_admin)])


@router.get("")
def list_users(session: DbDep) -> list[UserResponse]:
    """List all active HR agent users."""
    return user_service.list_users(session)


@router.post("", status_code=201)
def register_user(body: UserCreate, session: DbDep) -> UserResponse:
    """Register an employee as an HR agent user with a given role."""
    return user_service.register_user(session, body)


@router.delete("/{user_id}", status_code=204)
def deregister_user(user_id: int, session: DbDep):
    """Deactivate a user (soft delete)."""
    user_service.deregister_user(session, user_id)
