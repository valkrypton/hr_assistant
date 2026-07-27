"""ScopePromptBuilder — renders the natural-language access-control block injected
into the agent prefix so the LLM enforces access at generation time. Split out of
RBACContext (SRP). Output must stay character-identical to the previous
RBACContext.scope_prompt()."""

from __future__ import annotations

from core.rbac.policy import ScopePolicy
from core.rbac.redaction import FORBIDDEN_COLUMNS
from core.rbac.roles import Role


class ScopePromptBuilder:
    def __init__(self, policy: ScopePolicy) -> None:
        self._p = policy

    def build(self) -> str:
        p = self._p
        forbidden_list = ", ".join(sorted(FORBIDDEN_COLUMNS))

        base = (
            f"FORBIDDEN COLUMNS — never include in any response regardless of what "
            f"the user asks: {forbidden_list}.\n"
        )

        if p.is_unrestricted:
            return base + "DATA SCOPE: full company-wide access.\n"

        if p.role == Role.DEPT_HEAD:
            if p.department_id:
                return (
                    base
                    + f"DATA SCOPE: you ONLY have access to department_id = {p.department_id}.\n"
                    f"- Every query MUST include a WHERE or JOIN condition restricting results to department_id = {p.department_id}.\n"
                    f"- If the question asks about any other department or team outside your department, "
                    f'respond ONLY with: "You don\'t have access to data outside your department."\n'
                    f"- Never query or return employee data from any other department.\n"
                )
            # Misconfigured — degrade to no access rather than full access.
            return base + "DATA SCOPE: no department assigned — return no employee data.\n"

        if p.role == Role.TEAM_LEAD:
            if p.team_id:
                return (
                    base + f"DATA SCOPE: you ONLY have access to team.id = {p.team_id} "
                    f"(via person_team.nsubteam_id = {p.team_id}).\n"
                    f"- Every query MUST include a JOIN to person_team WHERE nsubteam_id = {p.team_id} "
                    f"AND end_date IS NULL AND is_active = true.\n"
                    f"- If the question asks about any other team or employees outside your team, "
                    f'respond ONLY with: "You don\'t have access to data outside your team."\n'
                    f"- Never query or return employee data for any other team "
                    f"(person_team.nsubteam_id / team.id).\n"
                )
            return base + "DATA SCOPE: no team assigned — return no employee data.\n"

        return base + "DATA SCOPE: unknown or unsupported role — return no employee data.\n"
