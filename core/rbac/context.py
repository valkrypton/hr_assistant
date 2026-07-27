"""
RBACContext — carries the requesting user's identity and enforces data scope.

Usage
-----
    ctx = RBACContext.for_user(hr_user)
    answer = query(user_input, rbac_ctx=ctx)

Scope rules (FR-5.3 – FR-5.7):
    CTO_CEO    → no restrictions
    HR_MANAGER → no restrictions
    DEPT_HEAD  → own department only (department_id must be set on HRUser)
    TEAM_LEAD  → own team only      (team_id must be set on HRUser)

Forbidden columns (FR-5.8 — never exposed regardless of role):
    salary, compensation, NIC, bank details, personal phone/email, home address,
    date of birth.  These are injected into the agent prompt so the LLM refuses
    to include them in any response.

This module is now a thin façade: identity + authorization live in
core.rbac.policy.ScopePolicy, prompt rendering in core.rbac.prompt, and output
redaction in core.rbac.redaction. RBACContext composes them so the ~20 existing
call sites keep the same API.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from core.rbac.policy import ScopePolicy
from core.rbac.prompt import ScopePromptBuilder
from core.rbac.redaction import FORBIDDEN_COLUMNS, ForbiddenColumnRedactor
from core.rbac.roles import Role

if TYPE_CHECKING:
    from core.rbac.models import HRUser

__all__ = ["RBACContext", "FORBIDDEN_COLUMNS"]

# Shared, stateless redactor — the forbidden set is a fixed global rule.
_REDACTOR = ForbiddenColumnRedactor()


@dataclass(frozen=True)
class RBACContext(ScopePolicy):
    """Façade over ScopePolicy adding user-facing construction, the scope prompt,
    and output redaction. Inherits role/employee_id/department_id/team_id and the
    is_unrestricted / can_see_employee authorization checks from ScopePolicy."""

    @classmethod
    def for_user(cls, user: HRUser) -> RBACContext:
        return cls(
            role=Role(user.role),
            employee_id=user.employee_id,
            department_id=user.department_id,
            team_id=user.team_id,
        )

    @classmethod
    def superuser(cls) -> RBACContext:
        """Convenience context for CTO/CEO — full access, used in tests."""
        return cls(role=Role.CTO_CEO)

    def scope_prompt(self) -> str:
        """Prompt fragment describing what this user may and may not see."""
        return ScopePromptBuilder(self).build()

    def strip_forbidden(self, text: str) -> str:
        """Best-effort redaction of forbidden column names from agent output."""
        return _REDACTOR.strip(text)
