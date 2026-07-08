"""AgentContext — the per-request identity object passed through the pipeline.

Wraps (does not replace) RBACContext: the SQL guard and scope logic keep taking
the exact RBACContext they always have (`ctx.rbac`), so the RBAC test suite is
untouched. AgentContext adds the request-level fields the pipeline and tools
need — materialized permissions, a request id, and free-form metadata.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from uuid import uuid4

from core.policies import permissions_for
from core.rbac.context import RBACContext

if TYPE_CHECKING:
    from core.rbac.models import HRUser
    from core.rbac.roles import Role


@dataclass(frozen=True)
class AgentContext:
    rbac: RBACContext
    slack_user_id: str | None = None
    permissions: frozenset[str] = field(default_factory=frozenset)
    request_id: str = field(default_factory=lambda: uuid4().hex)
    metadata: dict = field(default_factory=dict)

    @classmethod
    def for_user(cls, user: HRUser, slack_user_id: str | None = None) -> AgentContext:
        rbac = RBACContext.for_user(user)
        return cls(
            rbac=rbac,
            slack_user_id=slack_user_id or user.slack_user_id,
            permissions=permissions_for(rbac.role),
        )

    @classmethod
    def for_rbac(cls, rbac: RBACContext, slack_user_id: str | None = None) -> AgentContext:
        return cls(
            rbac=rbac,
            slack_user_id=slack_user_id,
            permissions=permissions_for(rbac.role),
        )

    @property
    def role(self) -> Role:
        return self.rbac.role

    @property
    def employee_id(self) -> int | None:
        return self.rbac.employee_id
