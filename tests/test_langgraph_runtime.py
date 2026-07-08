"""
Tests for the LangGraph agent runtime (PR6 — additive; default AGENT_RUNTIME
stays "legacy", so this runtime is only exercised by tests until PR7's cutover).

Covers:
  - build_system_prompt(): restricted-role scope block, query_erp_sql
    reference (not sql_db_query), hr_records_available toggling
  - LangGraphRuntime.run() happy path: answer extraction, tools_used, token
    aggregation, sql_statements/tables_accessed from the SqlCollector
  - Scope injection through the tool (KEY security test): the guard scopes
    SQL from the resolved identity, not from anything the fake LLM asked for
  - Guard rejection surfaces as an "Error: ..." tool observation, not a crash;
    rejected SQL is not recorded
  - to_langchain_tools() permission denial short-circuits before Tool.fn runs
  - Token fields default to 0 when usage_metadata is absent (no crash)

No real LLM involved — a GenericFakeChatModel stands in for the model, with
bind_tools() no-op'd so create_agent can use it. Same seeded-ERP pattern as
tests/test_tools_sql.py / tests/test_scope_execution.py.
"""

import sqlite3

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from pydantic import BaseModel, Field

from core.config import settings
from core.db import erp_engine
from core.identity.context import AgentContext
from core.rbac.context import RBACContext
from core.rbac.roles import Role
from core.runtimes import langgraph as langgraph_pkg  # noqa: F401  (package sanity)
from core.runtimes.base import AgentRunRequest
from core.runtimes.langgraph import runtime as runtime_mod
from core.runtimes.langgraph.prompts import build_system_prompt
from core.runtimes.langgraph.runtime import LangGraphRuntime
from core.runtimes.langgraph.tool_adapter import to_langchain_tools
from core.tools.base import Tool

# Seed layout mirrors tests/test_scope_execution.py / tests/test_tools_sql.py:
#     dept 3 (Engineering) = {101, 102}
#     dept 4 (Sales)       = {103, 104}
_SCHEMA = """
CREATE TABLE department (id INTEGER PRIMARY KEY, name TEXT);
CREATE TABLE person (
    id INTEGER PRIMARY KEY, full_name TEXT, department_id INTEGER,
    status_id INTEGER, is_active INTEGER, joining_date TEXT, separation_date TEXT
);
"""

_SEED = [
    "INSERT INTO department VALUES (3, 'Engineering'), (4, 'Sales')",
    """INSERT INTO person (id, full_name, department_id, status_id, is_active) VALUES
        (101, 'Alice', 3, 10, 1),
        (102, 'Bob',   3, 10, 1),
        (103, 'Carol', 4, 10, 1),
        (104, 'Dave',  4, 10, 1)""",
]

DEPT3_NAMES = {"Alice", "Bob"}


class FakeToolCallingModel(GenericFakeChatModel):
    """GenericFakeChatModel plays back a fixed message sequence. create_agent
    calls bind_tools() while building the graph; no-op it so the fake model
    (which doesn't natively support tool binding) can stand in for a real
    tool-calling LLM."""

    def bind_tools(self, *args, **kwargs):
        return self


@pytest.fixture()
def erp_db(tmp_path):
    """Seeded temp-FILE SQLite DB — execute_guarded_sql opens its own
    connection via erp_engine(), so a :memory: DB would not be visible to it."""
    db_path = tmp_path / "erp.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(_SCHEMA)
    for stmt in _SEED:
        conn.execute(stmt)
    conn.commit()
    conn.close()

    orig = settings.DATABASE_URL
    settings.DATABASE_URL = f"sqlite:///{db_path}"
    erp_engine.cache_clear()
    try:
        yield
    finally:
        settings.DATABASE_URL = orig
        erp_engine.cache_clear()


def _tool_call_message(sql: str, call_id: str = "call_1", **usage) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"name": "query_erp_sql", "args": {"sql": sql}, "id": call_id}],
        usage_metadata=usage or None,
    )


def _final_message(text: str, **usage) -> AIMessage:
    return AIMessage(content=text, usage_metadata=usage or None)


def _patch_llm(monkeypatch, messages: list) -> None:
    """messages is consumed by iter() per run — pass a fresh list per test/call."""
    monkeypatch.setattr(
        runtime_mod, "get_llm", lambda: FakeToolCallingModel(messages=iter(messages))
    )


# ---------------------------------------------------------------------------
# build_system_prompt()
# ---------------------------------------------------------------------------


class TestBuildSystemPrompt:
    def test_restricted_role_prompt_has_scope_block_and_query_erp_sql_reference(self):
        ctx = RBACContext(role=Role.DEPT_HEAD, department_id=3)
        prompt = build_system_prompt(ctx, hr_records_available=True)

        assert "department_id = 3" in prompt
        assert "DATA SCOPE" in prompt
        assert "query_erp_sql" in prompt
        assert "sql_db_query" not in prompt

    def test_hr_records_available_false_includes_no_table_note(self):
        ctx = RBACContext(role=Role.DEPT_HEAD, department_id=3)
        prompt = build_system_prompt(ctx, hr_records_available=False)

        assert "NO hr_records TABLE" in prompt

    def test_hr_records_available_true_omits_no_table_note(self):
        ctx = RBACContext(role=Role.DEPT_HEAD, department_id=3)
        prompt = build_system_prompt(ctx, hr_records_available=True)

        assert "NO hr_records TABLE" not in prompt

    def test_unrestricted_role_gets_unrestricted_block_not_scope_restriction(self):
        prompt = build_system_prompt(RBACContext.superuser(), hr_records_available=True)

        assert "UNRESTRICTED" in prompt
        assert "query_erp_sql" in prompt
        assert "sql_db_query" not in prompt


# ---------------------------------------------------------------------------
# LangGraphRuntime.run() — happy path
# ---------------------------------------------------------------------------


class TestRunHappyPath:
    def test_returns_answer_tools_used_tokens_and_sql_from_collector(self, monkeypatch, erp_db):
        monkeypatch.setattr(runtime_mod, "_hr_records_cache", False)
        _patch_llm(
            monkeypatch,
            [
                _tool_call_message(
                    "SELECT id FROM person", input_tokens=10, output_tokens=5, total_tokens=15
                ),
                _final_message(
                    "There are 4 employees.",
                    input_tokens=20,
                    output_tokens=8,
                    total_tokens=28,
                ),
            ],
        )

        result = LangGraphRuntime().run(
            AgentRunRequest(question="How many employees?", rbac_ctx=RBACContext.superuser())
        )

        assert result.answer == "There are 4 employees."
        assert result.tools_used == ["query_erp_sql"]
        assert result.prompt_tokens == 30
        assert result.completion_tokens == 13
        assert result.total_tokens == 43
        assert len(result.sql_statements) == 1
        assert "person" in result.sql_statements[0]
        assert "person" in result.tables_accessed


# ---------------------------------------------------------------------------
# Scope injection through the tool — the key security test
# ---------------------------------------------------------------------------


class TestScopeInjectionThroughTool:
    def test_dept_head_scope_comes_from_identity_not_the_llm(self, monkeypatch, erp_db):
        monkeypatch.setattr(runtime_mod, "_hr_records_cache", False)
        # The fake model asks for ALL rows — no department filter at all.
        _patch_llm(
            monkeypatch,
            [
                _tool_call_message("SELECT full_name FROM person"),
                _final_message("Alice, Bob."),
            ],
        )

        dept_head_ctx = RBACContext(role=Role.DEPT_HEAD, department_id=3)
        result = LangGraphRuntime().run(
            AgentRunRequest(question="List everyone.", rbac_ctx=dept_head_ctx)
        )

        # The executed SQL was rewritten by the guard to add the scope
        # predicate — the LLM never asked for this, and could not remove it.
        assert len(result.sql_statements) == 1
        assert "department_id = 3" in result.sql_statements[0]

        # The tool's actual return value (visible via the resulting answer
        # text we fed back through the fake model) reflects only dept 3.
        # Verify at the data layer too: rerun the recorded, guard-rewritten
        # SQL directly against the seeded ERP and confirm it only touches
        # dept-3 rows.
        with erp_engine().connect() as conn:
            import sqlalchemy

            rows = conn.execute(sqlalchemy.text(result.sql_statements[0])).fetchall()
        names = {r[0] for r in rows}
        assert names == DEPT3_NAMES


# ---------------------------------------------------------------------------
# Guard rejection surfaces as an observation, not a crash
# ---------------------------------------------------------------------------


class TestGuardRejectionDoesNotCrash:
    def test_forbidden_sql_returns_normal_result_and_is_not_recorded(self, monkeypatch, erp_db):
        monkeypatch.setattr(runtime_mod, "_hr_records_cache", False)
        _patch_llm(
            monkeypatch,
            [
                _tool_call_message("SELECT salary FROM person"),
                _final_message("That information is not available."),
            ],
        )

        result = LangGraphRuntime().run(
            AgentRunRequest(question="What is Alice's salary?", rbac_ctx=RBACContext.superuser())
        )

        assert result.answer == "That information is not available."
        assert result.sql_statements == []
        assert result.tables_accessed == ""


# ---------------------------------------------------------------------------
# to_langchain_tools() — permission denial
# ---------------------------------------------------------------------------


class _NoopInput(BaseModel):
    x: str = Field(default="")


def _noop_fn(ctx, x: str = "") -> str:  # pragma: no cover - should never run
    return "should not be reached"


class TestPermissionDenied:
    def test_tool_requiring_unheld_permission_returns_error_denied(self):
        forbidden_tool = Tool(
            name="forbidden_tool",
            description="requires a permission nobody holds",
            input_model=_NoopInput,
            required_permissions=("nonexistent.perm",),
            fn=_noop_fn,
        )
        team_lead_ctx = AgentContext.for_rbac(RBACContext(role=Role.TEAM_LEAD, team_id=7))

        lc_tools = to_langchain_tools(team_lead_ctx, [forbidden_tool])
        assert len(lc_tools) == 1

        observation = lc_tools[0].invoke({"x": "anything"})
        assert observation.startswith("Error: permission denied")


# ---------------------------------------------------------------------------
# Token fields default to 0 when usage_metadata is absent
# ---------------------------------------------------------------------------


class TestUsageMetadataAbsent:
    def test_no_usage_metadata_yields_zero_tokens_without_crashing(self, monkeypatch, erp_db):
        monkeypatch.setattr(runtime_mod, "_hr_records_cache", False)
        # No usage_metadata on either message at all.
        _patch_llm(
            monkeypatch,
            [AIMessage(content="There are 4 employees.")],
        )

        result = LangGraphRuntime().run(
            AgentRunRequest(question="How many employees?", rbac_ctx=RBACContext.superuser())
        )

        assert result.answer == "There are 4 employees."
        assert result.prompt_tokens == 0
        assert result.completion_tokens == 0
        assert result.total_tokens == 0
