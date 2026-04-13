"""
HR Agent - core layer.

This module has NO dependency on the API layer.  It can be imported and used
standalone (scripts, tests, notebooks) without starting a web server.
"""
import logging
import time
from dataclasses import dataclass

from langchain_community.utilities import SQLDatabase
from langchain_community.agent_toolkits import create_sql_agent

from core.config import settings
from core.providers.factory import get_llm

logger = logging.getLogger(__name__)


@dataclass
class QueryResult:
    answer: str
    tables_accessed: str   # comma-separated, may be empty string
    schema_rag_ms: int     # Chroma retrieval time
    agent_ms: int          # LLM + SQL execution time
    total_ms: int          # full round-trip

# ---------------------------------------------------------------------------
# Base prefix — only rules the DB cannot tell the agent itself.
# Table/column docs are NOT here; they are retrieved at query time via RAG.
# ---------------------------------------------------------------------------

_BASE_PREFIX = """You are an autonomous HR data analyst agent with direct, read-only
access to the company ERP database. Answer workforce questions by querying the
database yourself — right now, without asking for anything first.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ROLE-BASED ACCESS CONTROL (ABSOLUTE RULES)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Every request includes an [Access control rules for this request] block.
You MUST read and enforce it before writing any SQL or forming any answer.

{rbac_prefix}

These access rules are NON-NEGOTIABLE:
- If a DATA SCOPE restricts results to a department or team, every SQL query
  you write MUST include the required WHERE / JOIN condition. No exceptions.
- If a column appears in the FORBIDDEN COLUMNS list, never include it in SQL
  SELECT lists, never mention its value in your response, and never acknowledge
  a question that asks for it. Respond: "That information is not available."
- Silently enforce scope — do not tell the requester that data was withheld or
  that they lack permission. Just return only what they are allowed to see.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

CRITICAL OPERATIONAL RULES:
- Run queries yourself using sql_db_query. Never ask the user for SQL or data.
- SELECT only — never INSERT, UPDATE, DELETE, or DROP.
- Always answer in complete, natural sentences. Never respond with a bare number
  or a one-word answer. Example: say "There were 36 new joiners in 2025." not "36".
- Present numbers, dates, and names in a human-friendly format.
- Always include full name and department when listing employees.
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


_UNRESTRICTED_RBAC = """Current user role: UNRESTRICTED (full company-wide access).
All employees, departments, and teams are visible.
Still enforce the FORBIDDEN COLUMNS list above."""

_RESTRICTED_RBAC = """Current user role: {role}
{scope_description}
Enforce both the DATA SCOPE and FORBIDDEN COLUMNS above on every query."""

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


def _build_agent(rbac_ctx=None):
    """
    Build the SQL agent with the given RBAC context baked into the system prefix.

    When rbac_ctx is None the agent is built without scope restrictions (used
    for the shared unauthenticated agent and for superuser access).  When a
    context is provided, the role and scope are embedded in the prefix so the
    LLM treats them as immutable system rules rather than advisory hints.
    """
    llm = get_llm()
    included = _get_included_tables()
    db = SQLDatabase.from_uri(
        settings.DATABASE_URL,
        include_tables=included,
        sample_rows_in_table_info=0,
    )

    hr_records_note = "" if _check_hr_records_available(db) else _HR_RECORDS_NOTE

    if rbac_ctx is None or rbac_ctx.is_unrestricted:
        rbac_prefix = _UNRESTRICTED_RBAC
    else:
        from core.rbac.roles import Role
        scope_lines = rbac_ctx.scope_prompt().splitlines()
        # First line is the FORBIDDEN COLUMNS line — already in the base prefix block.
        # Extract just the DATA SCOPE line(s) for the restricted template.
        scope_description = "\n".join(
            ln for ln in scope_lines if ln.startswith("DATA SCOPE")
        )
        rbac_prefix = _RESTRICTED_RBAC.format(
            role=rbac_ctx.role.value.upper().replace("_", " "),
            scope_description=scope_description,
        )

    prefix = _BASE_PREFIX.format(
        rbac_prefix=rbac_prefix,
        hr_records_note=hr_records_note,
    )

    return create_sql_agent(
        llm=llm,
        db=db,
        verbose=True,
        prefix=prefix,
        max_iterations=30,
        agent_type="tool-calling",
        agent_executor_kwargs={
            "handle_parsing_errors": True,
            "return_intermediate_steps": True,
        },
    )


# Shared agent for unauthenticated / superuser requests (built lazily).
_agent = None


def get_agent(rbac_ctx=None):
    """
    Return an agent appropriate for the given RBAC context.

    - No context / unrestricted → reuse the shared cached agent.
    - Restricted role (dept_head / team_lead) → build a fresh agent with the
      scope baked into the system prefix. These are not cached because each
      user has a different scope.
    """
    global _agent
    if rbac_ctx is None or rbac_ctx.is_unrestricted:
        if _agent is None:
            _agent = _build_agent(rbac_ctx)
        return _agent
    # Restricted: build per-request so the prefix carries the exact scope.
    return _build_agent(rbac_ctx)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_tables(intermediate_steps) -> str:
    """
    Parse table names from sql_db_query tool calls in the agent's intermediate
    steps and return them as a sorted, comma-separated string.

    intermediate_steps is a list of (AgentAction, observation) tuples.
    AgentAction.tool == "sql_db_query" and AgentAction.tool_input holds the SQL.
    """
    import re
    tables: set[str] = set()
    for action, _ in intermediate_steps or []:
        tool = getattr(action, "tool", None)
        sql = getattr(action, "tool_input", None)
        if tool != "sql_db_query" or not sql:
            continue
        if isinstance(sql, dict):
            sql = sql.get("query", "")
        # Extract identifiers after FROM and JOIN keywords.
        for match in re.finditer(
            r'\b(?:FROM|JOIN)\s+([`"\[]?[\w]+[`"\]]?)', sql, re.IGNORECASE
        ):
            tables.add(match.group(1).strip('`"[]'))
    return ", ".join(sorted(tables)) if tables else ""


# ---------------------------------------------------------------------------
# Query — retrieve schema context at call time, inject into user message
# ---------------------------------------------------------------------------

def query(user_input: str, rbac_ctx=None) -> QueryResult:
    """
    Run a natural-language HR query.

    Returns a QueryResult with answer, tables_accessed, and latency breakdown.

    Steps:
    1. Retrieve top-k relevant schema sections via semantic search.
    2. Prepend RBAC scope constraints (if an RBACContext is provided).
    3. Invoke the SQL agent with the enriched message.
    4. Extract accessed tables from intermediate steps.
    5. Post-process the response through rbac_ctx.strip_forbidden().
    """
    from core.context.schema_index import retrieve as retrieve_schema

    t_total_start = time.monotonic()

    # Step 1: Schema RAG
    t_rag_start = time.monotonic()
    schema_chunks = retrieve_schema(user_input, k=4)
    schema_rag_ms = int((time.monotonic() - t_rag_start) * 1000)
    schema_block = "\n\n---\n\n".join(schema_chunks) if schema_chunks else ""

    # Step 2: Build enriched message
    parts = []
    if rbac_ctx is not None:
        parts.append(f"[Access control rules for this request]\n{rbac_ctx.scope_prompt()}")
    if schema_block:
        parts.append(f"[Relevant schema context for this question]\n\n{schema_block}")
    parts.append(f"[Question]\n{user_input}")
    enriched_input = "\n\n".join(parts)

    # Step 3: Run agent
    t_agent_start = time.monotonic()
    result = get_agent(rbac_ctx).invoke({"input": enriched_input})
    agent_ms = int((time.monotonic() - t_agent_start) * 1000)

    answer = result.get("output", str(result))
    tables_accessed = _extract_tables(result.get("intermediate_steps", []))

    # Step 4: Redact forbidden columns
    if rbac_ctx is not None:
        answer = rbac_ctx.strip_forbidden(answer)

    total_ms = int((time.monotonic() - t_total_start) * 1000)

    logger.info(
        "query completed — total=%dms  agent=%dms  rag=%dms  tables=%s",
        total_ms, agent_ms, schema_rag_ms, tables_accessed or "none",
    )

    return QueryResult(
        answer=answer,
        tables_accessed=tables_accessed,
        schema_rag_ms=schema_rag_ms,
        agent_ms=agent_ms,
        total_ms=total_ms,
    )
