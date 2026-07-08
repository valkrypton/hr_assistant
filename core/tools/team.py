"""team_roster — current members of a team (SPEC #8).

Encodes the non-discoverable "current assignment" rule
(person_team.end_date IS NULL AND is_active) and the nsubteam_id -> team.id FK.
Scope (department/team restriction, forbidden columns) is applied by the guard
via run_via_guard, so this tool never reimplements RBAC.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from core.tools.base import Tool
from core.tools.sql import run_via_guard, sql_literal


class _Input(BaseModel):
    team_name: str | None = Field(
        default=None,
        description="Filter to a specific team/project by name. Omit for all teams in scope.",
    )


def _run(ctx, team_name: str | None = None) -> str:
    where = ["p.is_active = true", "pt.end_date IS NULL", "pt.is_active = true"]
    if team_name:
        where.append(f"t.name = {sql_literal(team_name)}")
    sql = (
        "SELECT p.full_name, d.name, t.name, pt.billable "
        "FROM person p "
        "JOIN department d ON p.department_id = d.id "
        "JOIN person_team pt ON pt.person_id = p.id "
        "JOIN team t ON pt.nsubteam_id = t.id "
        f"WHERE {' AND '.join(where)} "
        "ORDER BY t.name, p.full_name"
    )
    return run_via_guard(ctx, sql)


TEAM_ROSTER = Tool(
    name="team_roster",
    description=(
        "Current roster for a team/project: each active member's name, department, "
        "team, and whether their assignment is billable. Reflects only current "
        "assignments. Optionally filter by team name."
    ),
    input_model=_Input,
    required_permissions=("employee.team.read",),
    fn=_run,
)
