"""Protocol seams so services depend on abstractions, not concrete
implementations (DIP). The concrete `core.agent.query` and `HRUserRepository`
satisfy these structurally; tests inject fakes."""

from typing import Protocol

from sqlalchemy.orm import Session

from core.agent import AgentQueryResult
from core.rbac.context import RBACContext
from core.rbac.models import HRUser


class AgentRunner(Protocol):
    def __call__(self, query: str, rbac_ctx: RBACContext | None = None) -> AgentQueryResult: ...


class UserRepo(Protocol):
    def get_by_slack_user_id(self, session: Session, slack_user_id: str) -> HRUser | None: ...
