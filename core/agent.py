"""
HR Agent - core layer.

This module has NO dependency on the API layer.  It can be imported and used
standalone (scripts, tests, notebooks) without starting a web server.
"""

from langchain_community.utilities import SQLDatabase
from langchain_community.agent_toolkits import create_sql_agent

from core.config import settings
from core.providers.factory import get_llm

# ---------------------------------------------------------------------------
# Base prefix — only rules the DB cannot tell the agent itself.
# Table/column docs are NOT here; they are retrieved at query time via RAG.
# ---------------------------------------------------------------------------

_BASE_PREFIX = """You are an autonomous HR data analyst agent with direct, read-only
access to the company ERP database. Answer workforce questions by querying the
database yourself — right now, without asking for anything first.

CRITICAL RULES:
- Run queries yourself using sql_db_query. Never ask the user for SQL or data.
- SELECT only — never INSERT, UPDATE, DELETE, or DROP.
- Present numbers, dates, and names in a human-friendly format.
- Always include full name and department when listing employees.
- NEVER expose in any response: salary, compensation, NIC numbers, bank details,
  personal phone numbers, personal email, home addresses, or date of birth.
- Provide only the direct answer — no narration, no SQL in the response, no
  "Running query now" commentary.

NON-DISCOVERABLE BUSINESS RULES (memorise these):

Status IDs (person.status_id — the status table is not queryable):
  10=Active  22=Active-B(Bench)  17=Probation
  11=Resigned  12=Terminated  14=Laid off  20=End of contract  13=Inactive

Employment classification (employment_type.type):
  employed     → type IN (1=Employee, 4=Intern, 5=EOR)
  subcontractor → type IN (2=Contract, 3=Sub-contractor)

Column name gotchas:
  - Employee name  : person.full_name  (NOT first_name / last_name)
  - Hire date      : person.joining_date
  - Exit date      : person.separation_date  (NULL = still employed)
  - Separation type: 2=Resignation  3=Termination  4=End of Contract
  - Current team FK: person_team.nsubteam_id  → team.id
  - Approved leave : leave_record.status = 1
  - Log submitted  : person_week_log.is_completed = true
  - Log hours      : person_week_log.hours + person_week_log.minutes / 60.0
  - Current assignment: person_team WHERE end_date IS NULL AND is_active = true
  - Competency assessment: person_competency WHERE status = 2 AND is_enabled = true
{hr_records_note}
The schema context relevant to this specific question is provided in the user message."""

_HR_RECORDS_NOTE = """
NO hr_records TABLE: For warnings/disciplinary queries use these proxies instead:
  • core_personstatushistory — status transitions (e.g. moves to Inactive/Probation)
  • person_week_log — compliance gaps (is_completed = false)
  Always state in your response that direct HR warning records are unavailable."""


# ---------------------------------------------------------------------------
# Agent construction
# ---------------------------------------------------------------------------

def _check_hr_records_available(db: SQLDatabase) -> bool:
    try:
        db.run("SELECT 1 FROM hr_records LIMIT 1")
        return True
    except Exception:
        return False


def _get_included_tables() -> list[str]:
    if not settings.INCLUDED_TABLES:
        raise ValueError(
            "INCLUDED_TABLES must be set in .env. "
            "List only the tables the agent needs (e.g. person,department,leave_record)."
        )
    return list(settings.INCLUDED_TABLES)


def _build_agent():
    """Build the SQL agent — called once on first query."""
    llm = get_llm()
    included = _get_included_tables()
    db = SQLDatabase.from_uri(
        settings.DATABASE_URL,
        include_tables=included,
        sample_rows_in_table_info=0,
    )

    hr_records_note = "" if _check_hr_records_available(db) else _HR_RECORDS_NOTE
    prefix = _BASE_PREFIX.format(hr_records_note=hr_records_note)

    return create_sql_agent(
        llm=llm,
        db=db,
        verbose=True,
        prefix=prefix,
        max_iterations=30,
        agent_type="tool-calling",
        agent_executor_kwargs={"handle_parsing_errors": True},
    )


_agent = None


def get_agent():
    """Return the shared agent instance, building it on first call."""
    global _agent
    if _agent is None:
        _agent = _build_agent()
    return _agent


# ---------------------------------------------------------------------------
# Query — retrieve schema context at call time, inject into user message
# ---------------------------------------------------------------------------

def query(user_input: str, rbac_ctx=None) -> str:
    """
    Run a natural-language HR query and return the answer string.

    Steps:
    1. Retrieve top-k relevant schema sections via semantic search.
    2. Prepend RBAC scope constraints (if an RBACContext is provided).
    3. Invoke the SQL agent with the enriched message.
    4. Post-process the response through rbac_ctx.strip_forbidden() as a
       defence-in-depth measure against prompt-injection or LLM non-compliance.
    """
    from core.context.schema_index import retrieve as retrieve_schema

    schema_chunks = retrieve_schema(user_input, k=4)
    schema_block = "\n\n---\n\n".join(schema_chunks) if schema_chunks else ""

    parts = []
    if rbac_ctx is not None:
        parts.append(f"[Access control rules for this request]\n{rbac_ctx.scope_prompt()}")
    if schema_block:
        parts.append(f"[Relevant schema context for this question]\n\n{schema_block}")
    parts.append(f"[Question]\n{user_input}")

    enriched_input = "\n\n".join(parts)

    result = get_agent().invoke({"input": enriched_input})
    answer = result.get("output", str(result))

    if rbac_ctx is not None:
        answer = rbac_ctx.strip_forbidden(answer)

    return answer
