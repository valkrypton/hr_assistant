"""ScopePolicy — the requester's identity plus the pure authorization checks
(is_unrestricted, can_see_employee). No prompt strings, no regex — SRP."""

from __future__ import annotations

from dataclasses import dataclass

from core.rbac.roles import Role


@dataclass(frozen=True)
class ScopePolicy:
    role: Role
    employee_id: int | None = None  # the requester's own person.id
    department_id: int | None = None  # set for DEPT_HEAD
    team_id: int | None = None  # set for TEAM_LEAD

    @property
    def is_unrestricted(self) -> bool:
        """True for roles with company-wide access."""
        return self.role in (Role.CTO_CEO, Role.HR_MANAGER)

    def can_see_employee(self, dept_id: int | None, team_id: int | None) -> bool:
        """Post-query check: can this user see a result row belonging to the given
        department/team?  Used to filter rows after the agent returns results."""
        if self.is_unrestricted:
            return True
        if self.role == Role.DEPT_HEAD:
            return self.department_id is not None and dept_id == self.department_id
        if self.role == Role.TEAM_LEAD:
            return self.team_id is not None and team_id == self.team_id
        return False
