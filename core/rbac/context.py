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
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, TYPE_CHECKING

from core.rbac.roles import Role

if TYPE_CHECKING:
    from core.rbac.models import HRUser


# Columns that must never appear in any agent response, regardless of role.
FORBIDDEN_COLUMNS: frozenset[str] = frozenset({
    "salary",
    "basic_salary",
    "gross_salary",
    "net_salary",
    "compensation",
    "nic",
    "cnic",
    "bank_account",
    "bank_details",
    "home_address",
    "personal_address",
    "personal_phone",
    "personal_email",
    "date_of_birth",
    "dob",
    "passport_number",
    "medical_record",
})


@dataclass(frozen=True)
class RBACContext:
    role: Role
    employee_id: Optional[int] = None  # the requester's own person.id
    department_id: Optional[int] = None  # set for DEPT_HEAD
    team_id: Optional[int] = None  # set for TEAM_LEAD

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def for_user(cls, user: "HRUser") -> "RBACContext":
        return cls(
            role=Role(user.role),
            employee_id=user.employee_id,
            department_id=user.department_id,
            team_id=user.team_id,
        )

    @classmethod
    def superuser(cls) -> "RBACContext":
        """Convenience context for CTO/CEO — full access, used in tests."""
        return cls(role=Role.CTO_CEO)

    # ------------------------------------------------------------------
    # Scope helpers
    # ------------------------------------------------------------------

    @property
    def is_unrestricted(self) -> bool:
        """True for roles with company-wide access."""
        return self.role in (Role.CTO_CEO, Role.HR_MANAGER)

    def scope_prompt(self) -> str:
        """
        Returns a prompt fragment describing what this user may and may not see.
        Injected into the agent prefix so the LLM enforces access at generation time.
        """
        forbidden_list = ", ".join(sorted(FORBIDDEN_COLUMNS))

        base = (
            f"FORBIDDEN COLUMNS — never include in any response regardless of what "
            f"the user asks: {forbidden_list}.\n"
        )

        if self.is_unrestricted:
            return base + "DATA SCOPE: full company-wide access.\n"

        if self.role == Role.DEPT_HEAD:
            if self.department_id:
                return (
                    base
                    + f"DATA SCOPE: you ONLY have access to department_id = {self.department_id}.\n"
                    f"- Every query MUST include a WHERE or JOIN condition restricting results to department_id = {self.department_id}.\n"
                    f"- If the question asks about any other department or team outside your department, "
                    f"respond ONLY with: \"You don't have access to data outside your department.\"\n"
                    f"- Never query or return employee data from any other department.\n"
                )
            # Misconfigured — degrade to no access rather than full access.
            return base + "DATA SCOPE: no department assigned — return no employee data.\n"

        if self.role == Role.TEAM_LEAD:
            if self.team_id:
                return (
                    base
                    + f"DATA SCOPE: you ONLY have access to team.id = {self.team_id} "
                    f"(via person_team.nsubteam_id = {self.team_id}).\n"
                    f"- Every query MUST include a JOIN to person_team WHERE nsubteam_id = {self.team_id} "
                    f"AND end_date IS NULL AND is_active = true.\n"
                    f"- If the question asks about any other team or employees outside your team, "
                    f"respond ONLY with: \"You don't have access to data outside your team.\"\n"
                    f"- Never query or return employee data for any other team "
                    f"(person_team.nsubteam_id / team.id).\n"
                )
            return base + "DATA SCOPE: no team assigned — return no employee data.\n"

        return base + "DATA SCOPE: unknown or unsupported role — return no employee data.\n"

    def can_see_employee(self, dept_id: Optional[int], team_id: Optional[int]) -> bool:
        """
        Post-query check: can this user see a result row belonging to the given
        department/team?  Used to filter rows after the agent returns results.
        """
        if self.is_unrestricted:
            return True
        if self.role == Role.DEPT_HEAD:
            return dept_id == self.department_id
        if self.role == Role.TEAM_LEAD:
            return team_id == self.team_id
        return False

    def strip_forbidden(self, text: str) -> str:
        """
        Best-effort scan of the agent's text output for forbidden column names.
        Returns the original text unchanged when nothing forbidden is found,
        otherwise returns a sanitised version with matching content redacted.

        This is a defence-in-depth measure — the primary enforcement is via the
        prompt.  This catches cases where the LLM ignores the instruction.
        """
        lower = text.lower()
        found = [col for col in FORBIDDEN_COLUMNS if col in lower]
        if not found:
            return text

        # Replace found tokens with redacted placeholders.
        sanitised = text
        for col in found:
            import re
            # Match the column name followed by its value (e.g. "salary: 120000").
            # Stop at comma, semicolon, or newline so we don't consume adjacent fields.
            sanitised = re.sub(
                rf"(?i){re.escape(col)}\s*[:\-=]?\s*[^\n,;]+",
                f"[{col.upper()} REDACTED]",
                sanitised,
            )
        return sanitised
