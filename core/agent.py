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
    tables_accessed: str      # comma-separated, may be empty string
    schema_rag_ms: int        # schema load time (file read, not RAG)
    agent_ms: int             # LLM + SQL execution time
    total_ms: int             # full round-trip
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

# ---------------------------------------------------------------------------
# Build the forbidden-columns string once from the canonical set in rbac/context.
# ---------------------------------------------------------------------------

def _forbidden_columns_str() -> str:
    from core.rbac.context import FORBIDDEN_COLUMNS
    return ", ".join(sorted(FORBIDDEN_COLUMNS))


# ---------------------------------------------------------------------------
# Base prefix — immutable rules the DB cannot supply.
# Full schema is injected at query time via the [Full schema context] block.
# ---------------------------------------------------------------------------

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
- Silently enforce scope — never tell the requester that data was withheld.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

OPERATIONAL RULES:
- Run queries yourself using sql_db_query. Never ask the user for SQL or data.
- SELECT only — never INSERT, UPDATE, DELETE, DROP, or ALTER.
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
        # The base prefix already includes the forbidden-columns rule; drop that line
        # here to avoid duplication. Retain ALL other scope/enforcement lines so
        # required JOINs/filters (e.g. nsubteam_id, end_date IS NULL) are not lost.
        scope_description = "\n".join(
            ln for ln in scope_lines
            if ln.strip() and not ln.startswith("FORBIDDEN COLUMNS")
        )
        rbac_prefix = _RESTRICTED_RBAC.format(
            role=rbac_ctx.role.value.upper().replace("_", " "),
            scope_description=scope_description,
        )

    prefix = _BASE_PREFIX.format(
        forbidden_columns=_forbidden_columns_str(),
        rbac_prefix=rbac_prefix,
        hr_records_note=hr_records_note,
    )

    return create_sql_agent(
        llm=llm,
        db=db,
        verbose=True,
        prefix=prefix,
        max_iterations=10,
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

def query(
    user_input: str,
    rbac_ctx=None,
    conversation_history: list[dict] | None = None,
) -> QueryResult:
    """
    Run a natural-language HR query.

    Args:
        user_input: The question to answer.
        rbac_ctx: Optional RBAC context scoping the response.
        conversation_history: Optional list of prior turns in the format
            [{"role": "user"|"assistant", "content": "..."}].
            Injected before the current question so the agent can resolve
            follow-up references (e.g. "who are the newest ones?").

    Returns a QueryResult with answer, tables_accessed, and latency breakdown.
    """
    from pathlib import Path

    t_total_start = time.monotonic()

    # Step 1: Load full schema — small enough (~3k tokens) to inject entirely.
    # No chunking/RAG needed; avoids lossy retrieval and Chroma dependency.
    t_rag_start = time.monotonic()
    _schema_path = Path(__file__).parent / "context" / "schema.md"
    schema_block = _schema_path.read_text() if _schema_path.exists() else ""
    schema_rag_ms = int((time.monotonic() - t_rag_start) * 1000)

    # Step 2: Build enriched message
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
        parts.append(f"[Conversation history — earlier turns in this thread]\n" + "\n".join(history_lines))
    parts.append(f"[Question]\n{user_input}")
    enriched_input = "\n\n".join(parts)

    # Step 3: Run agent with retry — up to 2 retries on transient failures.
    t_agent_start = time.monotonic()
    last_exc: Exception | None = None
    result = None
    prompt_tokens = completion_tokens = total_tokens = 0

    for attempt in range(3):
        if attempt > 0:
            wait = 2 ** attempt  # 2s, 4s
            logger.warning("Agent attempt %d failed, retrying in %ds: %s", attempt, wait, last_exc)
            time.sleep(wait)
        try:
            from langchain_community.callbacks import get_openai_callback
            with get_openai_callback() as cb:
                result = get_agent(rbac_ctx).invoke({"input": enriched_input})
            prompt_tokens = cb.prompt_tokens
            completion_tokens = cb.completion_tokens
            total_tokens = cb.total_tokens
            last_exc = None
            break
        except Exception as exc:
            last_exc = exc
            # Reset cached agent on failure so next attempt gets a fresh one.
            global _agent
            _agent = None

    if result is None:
        # All retries exhausted — return a user-friendly message, don't raise.
        logger.error("Agent failed after 3 attempts: %s", last_exc)
        total_ms = int((time.monotonic() - t_total_start) * 1000)
        return QueryResult(
            answer="Sorry, I wasn't able to process your request right now. Please try again in a moment.",
            tables_accessed="",
            schema_rag_ms=schema_rag_ms,
            agent_ms=int((time.monotonic() - t_agent_start) * 1000),
            total_ms=total_ms,
        )

    agent_ms = int((time.monotonic() - t_agent_start) * 1000)
    answer = result.get("output", str(result))
    tables_accessed = _extract_tables(result.get("intermediate_steps", []))

    # Step 4: Redact forbidden columns
    if rbac_ctx is not None:
        answer = rbac_ctx.strip_forbidden(answer)

    total_ms = int((time.monotonic() - t_total_start) * 1000)

    logger.info(
        "query completed — total=%dms  agent=%dms  rag=%dms  tokens=%d  tables=%s",
        total_ms, agent_ms, schema_rag_ms, total_tokens, tables_accessed or "none",
    )

    return QueryResult(
        answer=answer,
        tables_accessed=tables_accessed,
        schema_rag_ms=schema_rag_ms,
        agent_ms=agent_ms,
        total_ms=total_ms,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
    )
