"""Protocol seams so services depend on abstractions, not concrete
implementations (DIP). The concrete `core.agent.query` satisfies these
structurally; tests inject fakes."""

from typing import Protocol

from core.agent import AgentQueryResult
from core.rbac.context import RBACContext


class AgentRunner(Protocol):
    def __call__(self, query: str, rbac_ctx: RBACContext | None = None) -> AgentQueryResult: ...


class ScopeResolver(Protocol):
    def __call__(self, person_id: int) -> RBACContext | None: ...
