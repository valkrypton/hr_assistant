"""
Redaction and output-safety tests (HRASSISTAN-30).

RBAC scope enforcement itself now lives in tests/test_sql_guard_self_scope.py
(the DB-layer guard) and tests/test_scope_execution.py (row-level execution).
This file covers what's independent of access level:
  - FORBIDDEN_COLUMNS presence and immutability (FR-5.8)
  - strip_forbidden() redaction (defence-in-depth)
  - core.agent._extract_tables — sqlglot-based table extraction for audit
    logging (unrelated to RBAC; was co-located here historically)

No database or LLM is involved — all pure unit tests.
"""

import pytest

from core.rbac.context import FORBIDDEN_COLUMNS, RBACContext

# ---------------------------------------------------------------------------
# FR-5.8  Forbidden columns
# ---------------------------------------------------------------------------


class TestForbiddenColumns:
    @pytest.mark.parametrize(
        "col",
        [
            "salary",
            "nic",
            "bank_account",
            "date_of_birth",
            "personal_phone",
            "personal_email",
            "home_address",
        ],
    )
    def test_key_sensitive_columns_in_forbidden_set(self, col):
        assert col in FORBIDDEN_COLUMNS

    def test_forbidden_set_is_frozen(self):
        with pytest.raises((AttributeError, TypeError)):
            FORBIDDEN_COLUMNS.add("test")  # type: ignore


# ---------------------------------------------------------------------------
# strip_forbidden() — defence-in-depth redaction
# ---------------------------------------------------------------------------


class TestStripForbidden:
    def setup_method(self):
        self.ctx = RBACContext.unrestricted()

    def test_clean_text_passes_through(self):
        text = "Ali has been active since January."
        assert self.ctx.strip_forbidden(text) == text

    def test_salary_value_is_redacted(self):
        text = "Ali's salary: 120000 per year"
        result = self.ctx.strip_forbidden(text)
        assert "120000" not in result
        assert "REDACTED" in result

    def test_nic_value_is_redacted(self):
        text = "NIC: 42101-1234567-1"
        result = self.ctx.strip_forbidden(text)
        assert "42101" not in result

    def test_multiple_forbidden_fields_all_redacted(self):
        text = "salary: 90000, dob: 1990-01-01"
        result = self.ctx.strip_forbidden(text)
        assert "90000" not in result
        assert "1990-01-01" not in result

    def test_case_insensitive_detection(self):
        text = "SALARY: 80000"
        result = self.ctx.strip_forbidden(text)
        assert "80000" not in result

    def test_partial_word_not_redacted(self):
        # "nic" inside "clinic" must not trigger forbidden-column redaction.
        text = "The clinic handled the case."
        assert self.ctx.strip_forbidden(text) == text

    def test_unrelated_text_passes_through_unchanged(self):
        text = "Team velocity increased by 12% this sprint."
        assert self.ctx.strip_forbidden(text) == text


# ---------------------------------------------------------------------------
# core.agent._extract_tables — sqlglot-based table extraction for audit logging
# ---------------------------------------------------------------------------


class TestExtractTables:
    @staticmethod
    def make_step(sql: str):
        """Build a fake (AgentAction, observation) tuple for a sql_db_query call."""
        from types import SimpleNamespace

        return (SimpleNamespace(tool="sql_db_query", tool_input=sql), "observation")

    def extract(self, sql: str) -> str:
        from core.agent import _extract_tables

        return _extract_tables([self.make_step(sql)])

    def test_plain_from_and_join(self):
        result = self.extract(
            "SELECT * FROM person JOIN department ON person.department_id = department.id"
        )
        assert result == "department, person"

    def test_cte_alias_excluded_but_real_tables_reported(self):
        result = self.extract(
            "WITH x AS (SELECT * FROM person) "
            "SELECT * FROM x JOIN leave_record ON x.id = leave_record.person_id"
        )
        tables = result.split(", ")
        assert "person" in tables
        assert "leave_record" in tables
        assert "x" not in tables

    def test_unparseable_sql_falls_back_to_regex_without_raising(self):
        # sqlglot cannot parse this, but the regex fallback still finds "person"
        # after FROM — the important part is that no exception escapes.
        result = self.extract("SELECT * FROM person WHERE ((( totally $$ broken :::")
        assert result == "person"

    def test_no_sql_db_query_steps_returns_empty_string(self):
        from core.agent import _extract_tables

        assert _extract_tables([]) == ""
