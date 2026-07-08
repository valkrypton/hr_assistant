from fastapi import APIRouter, Depends

from api.deps import DbDep, require_admin
from api.schemas.users import UserCreate, UserResponse
from api.services import user_service

router = APIRouter(prefix="/users", dependencies=[Depends(require_admin)])


@router.get("", response_model=list[UserResponse])
def list_users(session: DbDep):
    """List all active HR agent users."""
    return user_service.list_users(session)


@router.post("", response_model=UserResponse, status_code=201)
def register_user(body: UserCreate, session: DbDep):
    """Register an employee as an HR agent user with a given role."""
    return user_service.register_user(session, body)


@router.delete("/{user_id}", status_code=204)
def deregister_user(user_id: int, session: DbDep):
    """Deactivate a user (soft delete)."""
    user_service.deregister_user(session, user_id)
