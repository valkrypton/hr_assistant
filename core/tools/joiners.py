"""joiners_summary — new-joiner counts for a year (SPEC #13, #14, #15).

Encodes the employed-vs-subcontractor classification (employment_type.type),
the approved-exit rule (users_personresignation.status = 1 + last_working_day),
and uses ISO date-range predicates (not EXTRACT) so the same SQL runs on
PostgreSQL and the SQLite test DB. Scope/forbidden columns come from the guard.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from core.tools.base import Tool
from core.tools.sql import run_via_guard

_GROUP_BY = {"none", "classification", "cohort_attrition"}


class _Input(BaseModel):
    year: int = Field(description="Calendar year of joining, e.g. 2025.")
    group_by: str = Field(
        default="none",
        description=(
            "'none' = total count; 'classification' = split employed vs subcontractor; "
            "'cohort_attrition' = count who both joined AND left in this year."
        ),
    )


def _year_range(year: int) -> tuple[str, str]:
    return f"'{year:04d}-01-01'", f"'{year + 1:04d}-01-01'"


def _run(ctx, year: int, group_by: str = "none") -> str:
    if group_by not in _GROUP_BY:
        return f"Error: group_by must be one of {sorted(_GROUP_BY)}."
    year = int(year)
    lo, hi = _year_range(year)
    joined = f"p.joining_date >= {lo} AND p.joining_date < {hi}"

    if group_by == "classification":
        sql = (
            "SELECT CASE WHEN et.type IN (1, 4, 5) THEN 'employed' ELSE 'subcontractor' END, "
            "COUNT(*) "
            "FROM person p "
            "JOIN employment_type et ON p.employment_type_id = et.id "
            f"WHERE {joined} "
            "GROUP BY CASE WHEN et.type IN (1, 4, 5) THEN 'employed' ELSE 'subcontractor' END"
        )
    elif group_by == "cohort_attrition":
        sql = (
            "SELECT COUNT(*) "
            "FROM person p "
            "JOIN users_personresignation upr ON upr.person_id = p.id AND upr.status = 1 "
            f"WHERE {joined} "
            f"AND upr.last_working_day >= {lo} AND upr.last_working_day < {hi}"
        )
    else:
        sql = f"SELECT COUNT(*) FROM person p WHERE {joined}"

    return run_via_guard(ctx, sql)


JOINERS_SUMMARY = Tool(
    name="joiners_summary",
    description=(
        "New-joiner statistics for a calendar year. group_by='none' returns the "
        "total; 'classification' splits employed vs subcontractor; "
        "'cohort_attrition' counts people who both joined and left in that year."
    ),
    input_model=_Input,
    required_permissions=("employee.headcount.read",),
    fn=_run,
)
