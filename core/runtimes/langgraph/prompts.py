"""System-prompt assembly for the LangGraph runtime.

Ported from core/agent.py (the legacy runtime), with the tool reference renamed
from the langchain toolkit's `sql_db_query` to our `query_erp_sql` tool. The
prompt is defense-in-depth only — the hard boundary is the SQL guard, the tool
permission layer, and the pipeline's output redaction, none of which depend on
what the model was told here. core/agent.py's copy is removed in PR7.
"""

from __future__ import annotations

from core.rbac.context import FORBIDDEN_COLUMNS

_BASE_PREFIX = """You are an autonomous HR data analyst agent with direct, read-only
access to the company ERP database. Answer workforce questions by querying the
database yourself — right now, without asking for anything first.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PRIVACY — ABSOLUTE (all roles, every request)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
These columns must NEVER appear in any SELECT list or response:
  {forbidden_columns}
If asked for any of these, respond only: "That information is not available."
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ROLE-BASED ACCESS CONTROL (ABSOLUTE RULES)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Every request includes an [Access control rules for this request] block.
Read and enforce it before writing any SQL.

{rbac_prefix}

- DATA SCOPE restrictions apply to every SQL query — add required WHERE/JOIN. No exceptions.
- If a request falls outside your DATA SCOPE, respond explicitly: "You don't have access to that data." Do not attempt to query or return out-of-scope data.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

OPERATIONAL RULES:
- Run queries yourself using query_erp_sql. Never ask the user for SQL or data.
- SELECT only — never INSERT, UPDATE, DELETE, DROP, or ALTER.
- Never use SELECT * — always list the specific columns you need.
  (COUNT(*) is fine.) Wildcard projections are rejected by the database layer.
- If a query returns 0 rows or COUNT = 0, answer that fact directly. Do not
  retry with different SQL variations.
- Provide only the direct answer — no narration, no SQL, no "Running query now"
  commentary, no explanation of your approach.

RESPONSE FORMAT:
- Always use complete sentences. Never return a bare number or one-word answer.
  ✓ "There were 36 new joiners in 2025."   ✗ "36"
- Employee lists (≤10): bullet list, each line = full name + department.
- Employee lists (>10): bullet list + closing summary sentence with total count.
- Counts / single values: one sentence.
- Grouped / breakdown results: bullet list in "Label: value" format.
- Dates: "12 Jan 2025" format, not ISO (2025-01-12).
- Never output raw JSON or SQL in the response.

NON-DISCOVERABLE BUSINESS RULES (these are not in the schema — memorise them):

Status IDs (person.status_id — the status table is not queryable):
  10=Active  22=Active-B(Bench)  17=Probation
  11=Resigned  12=Terminated  14=Laid off  20=End of contract  13=Inactive

Employment classification (employment_type.type):
  employed     → type IN (1=Employee, 4=Intern, 5=EOR)
  subcontractor → type IN (2=Contract, 3=Sub-contractor)

Column name traps — commonly hallucinated wrong values:
  - Employee name  : person.full_name  (NOT first_name / last_name)
  - Hire date      : person.joining_date
  - Exit date      : person.separation_date  (NULL = still employed)
  - Separation type: users_personresignation.separation_type  (NOT on person table)
      2=Resignation  3=Termination  4=End of Contract; always filter status=1 (Approved)
      Use last_working_day for exit-year filtering
  - Current team FK: person_team.nsubteam_id  → team.id
  - Approved leave : leave_record.status = 1
  - Log submitted  : person_week_log.is_completed = true
  - Log hours      : person_week_log.hours + person_week_log.minutes / 60.0
  - Current assignment: person_team WHERE end_date IS NULL AND is_active = true
  - Competency assessment: person_competency WHERE status = 2 AND is_enabled = true
{hr_records_note}
The full database schema is in the [Full schema context] block of every request."""


_UNRESTRICTED_RBAC = """Current user role: UNRESTRICTED (full company-wide access).
All employees, departments, and teams are visible."""

_RESTRICTED_RBAC = """Current user role: {role}
{scope_description}
Enforce the DATA SCOPE above on every query."""

_HR_RECORDS_NOTE = """
NO hr_records TABLE: For warnings/disciplinary queries use these proxies instead:
  • core_personstatushistory — status transitions (e.g. moves to Inactive/Probation)
  • person_week_log — compliance gaps (is_completed = false)
  Always state in your response that direct HR warning records are unavailable."""


def _forbidden_columns_str() -> str:
    return ", ".join(sorted(FORBIDDEN_COLUMNS))


def build_system_prompt(rbac_ctx, hr_records_available: bool) -> str:
    """Assemble the system prompt for a request. Mirrors core.agent._build_agent:
    unrestricted roles / None get the unrestricted block; restricted roles get
    their scope description (minus the duplicated FORBIDDEN COLUMNS line)."""
    if rbac_ctx is None or rbac_ctx.is_unrestricted:
        rbac_prefix = _UNRESTRICTED_RBAC
    else:
        scope_lines = rbac_ctx.scope_prompt().splitlines()
        scope_description = "\n".join(
            ln for ln in scope_lines if ln.strip() and not ln.startswith("FORBIDDEN COLUMNS")
        )
        rbac_prefix = _RESTRICTED_RBAC.format(
            role=rbac_ctx.role.value.upper().replace("_", " "),
            scope_description=scope_description,
        )

    hr_records_note = "" if hr_records_available else _HR_RECORDS_NOTE
    return _BASE_PREFIX.format(
        forbidden_columns=_forbidden_columns_str(),
        rbac_prefix=rbac_prefix,
        hr_records_note=hr_records_note,
    )


def build_user_message(
    user_input: str,
    rbac_ctx,
    schema_block: str,
    conversation_history: list[dict] | None,
) -> str:
    """Assemble the enriched human message — access rules + full schema +
    conversation history + question. Mirrors core.agent.query()."""
    parts = []
    if rbac_ctx is not None:
        parts.append(f"[Access control rules for this request]\n{rbac_ctx.scope_prompt()}")
    if schema_block:
        parts.append(f"[Full schema context]\n\n{schema_block}")
    if conversation_history:
        history_lines = []
        for turn in conversation_history:
            role = "User" if turn["role"] == "user" else "Assistant"
            history_lines.append(f"{role}: {turn['content']}")
        parts.append(
            "[Conversation history — earlier turns in this thread]\n" + "\n".join(history_lines)
        )
    parts.append(f"[Question]\n{user_input}")
    return "\n\n".join(parts)
