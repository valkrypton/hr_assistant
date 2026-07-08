"""leave_lookup — people on approved leave in a date range (SPEC #12).

Encodes the approved-leave rule (leave_record.status = 1) and inclusive
date-range overlap. Scope/forbidden-column enforcement comes from the guard.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from core.tools.base import Tool
from core.tools.sql import run_via_guard, sql_literal


class _Input(BaseModel):
    start_date: str = Field(description="Range start, inclusive, ISO date (YYYY-MM-DD).")
    end_date: str = Field(description="Range end, inclusive, ISO date (YYYY-MM-DD).")


def _run(ctx, start_date: str, end_date: str) -> str:
    # Overlap: a leave [lr.start, lr.end] intersects [start_date, end_date].
    sql = (
        "SELECT p.full_name, d.name, lr.start, lr.end "
        "FROM person p "
        "JOIN department d ON p.department_id = d.id "
        "JOIN leave_record lr ON lr.person_id = p.id AND lr.status = 1 "
        "WHERE p.is_active = true "
        f"AND lr.start <= {sql_literal(end_date)} AND lr.end >= {sql_literal(start_date)} "
        "ORDER BY lr.start, p.full_name"
    )
    return run_via_guard(ctx, sql)


LEAVE_LOOKUP = Tool(
    name="leave_lookup",
    description=(
        "People on APPROVED leave overlapping a date range: name, department, and "
        "leave start/end. Provide start_date and end_date as ISO dates."
    ),
    input_model=_Input,
    required_permissions=("employee.leave.read",),
    fn=_run,
)
