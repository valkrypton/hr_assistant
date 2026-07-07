"""
Role-boundary tests for RBAC (HRASSISTAN-30).

These tests cover:
  - Scope prompt content per role (FR-5.3 – FR-5.6)
  - is_unrestricted flag
  - Misconfigured restricted roles (missing dept/team ID)
  - FORBIDDEN_COLUMNS presence in every scope prompt (FR-5.8)
  - strip_forbidden() redaction (defence-in-depth)
  - RBACContext.for_user() factory
  - Agent prefix templates render without error
  - sql_guard.rewrite_sql() DB-layer scope enforcement

No database or LLM is involved — all pure unit tests.
"""
import pytest

from core.rbac.roles import Role
from core.rbac.context import RBACContext, FORBIDDEN_COLUMNS
from core.rbac.models import HRUser


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_ctx(role: Role, dept_id=None, team_id=None, emp_id=1) -> RBACContext:
    return RBACContext(role=role, employee_id=emp_id, department_id=dept_id, team_id=team_id)


# ---------------------------------------------------------------------------
# FR-5.3 / FR-5.4  CTO/CEO and HR Manager — unrestricted
# ---------------------------------------------------------------------------

class TestUnrestrictedRoles:
    @pytest.mark.parametrize("role", [Role.CTO_CEO, Role.HR_MANAGER])
    def test_is_unrestricted(self, role):
        ctx = make_ctx(role)
        assert ctx.is_unrestricted is True

    @pytest.mark.parametrize("role", [Role.CTO_CEO, Role.HR_MANAGER])
    def test_scope_prompt_says_full_access(self, role):
        prompt = make_ctx(role).scope_prompt()
        assert "full company-wide access" in prompt

    @pytest.mark.parametrize("role", [Role.CTO_CEO, Role.HR_MANAGER])
    def test_no_department_restriction_in_prompt(self, role):
        prompt = make_ctx(role, dept_id=99).scope_prompt()
        # Even if dept_id is set, unrestricted roles must NOT get a WHERE clause
        assert "department_id" not in prompt

    @pytest.mark.parametrize("role", [Role.CTO_CEO, Role.HR_MANAGER])
    def test_no_team_restriction_in_prompt(self, role):
        prompt = make_ctx(role, team_id=99).scope_prompt()
        assert "nsubteam_id" not in prompt


# ---------------------------------------------------------------------------
# FR-5.5  Department Head — own department only
# ---------------------------------------------------------------------------

class TestDeptHead:
    def test_is_restricted(self):
        assert make_ctx(Role.DEPT_HEAD, dept_id=3).is_unrestricted is False

    def test_scope_prompt_contains_department_id(self):
        prompt = make_ctx(Role.DEPT_HEAD, dept_id=3).scope_prompt()
        assert "department_id = 3" in prompt

    def test_scope_prompt_instructs_where_clause(self):
        prompt = make_ctx(Role.DEPT_HEAD, dept_id=3).scope_prompt()
        assert "WHERE" in prompt or "JOIN" in prompt

    def test_different_dept_ids_produce_different_prompts(self):
        p1 = make_ctx(Role.DEPT_HEAD, dept_id=1).scope_prompt()
        p2 = make_ctx(Role.DEPT_HEAD, dept_id=2).scope_prompt()
        assert p1 != p2

    def test_missing_dept_id_degrades_to_no_access(self):
        prompt = make_ctx(Role.DEPT_HEAD, dept_id=None).scope_prompt()
        assert "no department assigned" in prompt
        assert "no employee data" in prompt

    def test_no_team_scope_injected(self):
        prompt = make_ctx(Role.DEPT_HEAD, dept_id=5).scope_prompt()
        assert "nsubteam_id" not in prompt


# ---------------------------------------------------------------------------
# FR-5.6  Team Lead — own team only
# ---------------------------------------------------------------------------

class TestTeamLead:
    def test_is_restricted(self):
        assert make_ctx(Role.TEAM_LEAD, team_id=7).is_unrestricted is False

    def test_scope_prompt_contains_team_id(self):
        prompt = make_ctx(Role.TEAM_LEAD, team_id=7).scope_prompt()
        assert "nsubteam_id = 7" in prompt

    def test_scope_prompt_instructs_join(self):
        prompt = make_ctx(Role.TEAM_LEAD, team_id=7).scope_prompt()
        assert "JOIN" in prompt

    def test_scope_prompt_includes_active_filter(self):
        prompt = make_ctx(Role.TEAM_LEAD, team_id=7).scope_prompt()
        assert "end_date IS NULL" in prompt
        assert "is_active = true" in prompt

    def test_different_team_ids_produce_different_prompts(self):
        p1 = make_ctx(Role.TEAM_LEAD, team_id=1).scope_prompt()
        p2 = make_ctx(Role.TEAM_LEAD, team_id=2).scope_prompt()
        assert p1 != p2

    def test_missing_team_id_degrades_to_no_access(self):
        prompt = make_ctx(Role.TEAM_LEAD, team_id=None).scope_prompt()
        assert "no team assigned" in prompt
        assert "no employee data" in prompt


# ---------------------------------------------------------------------------
# FR-5.8  Forbidden columns — present in every role's scope prompt
# ---------------------------------------------------------------------------

class TestForbiddenColumns:
    @pytest.mark.parametrize("role,dept,team", [
        (Role.CTO_CEO, None, None),
        (Role.HR_MANAGER, None, None),
        (Role.DEPT_HEAD, 1, None),
        (Role.TEAM_LEAD, None, 1),
    ])
    def test_forbidden_columns_in_every_prompt(self, role, dept, team):
        prompt = make_ctx(role, dept_id=dept, team_id=team).scope_prompt()
        assert "FORBIDDEN COLUMNS" in prompt

    @pytest.mark.parametrize("col", ["salary", "nic", "bank_account", "date_of_birth",
                                      "personal_phone", "personal_email", "home_address"])
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
        self.ctx = make_ctx(Role.HR_MANAGER)

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


# ---------------------------------------------------------------------------
# can_see_employee() — post-query row visibility
# ---------------------------------------------------------------------------

class TestCanSeeEmployee:
    def test_cto_sees_any_employee(self):
        ctx = make_ctx(Role.CTO_CEO)
        assert ctx.can_see_employee(dept_id=99, team_id=99) is True

    def test_hr_manager_sees_any_employee(self):
        ctx = make_ctx(Role.HR_MANAGER)
        assert ctx.can_see_employee(dept_id=99, team_id=99) is True

    def test_dept_head_sees_own_dept(self):
        ctx = make_ctx(Role.DEPT_HEAD, dept_id=3)
        assert ctx.can_see_employee(dept_id=3, team_id=None) is True

    def test_dept_head_cannot_see_other_dept(self):
        ctx = make_ctx(Role.DEPT_HEAD, dept_id=3)
        assert ctx.can_see_employee(dept_id=4, team_id=None) is False

    def test_team_lead_sees_own_team(self):
        ctx = make_ctx(Role.TEAM_LEAD, team_id=7)
        assert ctx.can_see_employee(dept_id=None, team_id=7) is True

    def test_team_lead_cannot_see_other_team(self):
        ctx = make_ctx(Role.TEAM_LEAD, team_id=7)
        assert ctx.can_see_employee(dept_id=None, team_id=8) is False


# ---------------------------------------------------------------------------
# for_user() factory
# ---------------------------------------------------------------------------

class TestForUserFactory:
    def _make_hr_user(self, role, dept_id=None, team_id=None, emp_id=42):
        user = HRUser()
        user.employee_id = emp_id
        user.role = role.value
        user.department_id = dept_id
        user.team_id = team_id
        user.slack_user_id = "U_TEST"
        user.is_active = True
        return user

    def test_builds_correct_role(self):
        user = self._make_hr_user(Role.HR_MANAGER)
        ctx = RBACContext.for_user(user)
        assert ctx.role == Role.HR_MANAGER

    def test_builds_correct_department(self):
        user = self._make_hr_user(Role.DEPT_HEAD, dept_id=5)
        ctx = RBACContext.for_user(user)
        assert ctx.department_id == 5

    def test_builds_correct_team(self):
        user = self._make_hr_user(Role.TEAM_LEAD, team_id=12)
        ctx = RBACContext.for_user(user)
        assert ctx.team_id == 12

    def test_employee_id_preserved(self):
        user = self._make_hr_user(Role.CTO_CEO, emp_id=99)
        ctx = RBACContext.for_user(user)
        assert ctx.employee_id == 99


# ---------------------------------------------------------------------------
# Agent prefix template renders without error (smoke test)
# ---------------------------------------------------------------------------

class TestAgentPrefixRendering:
    @pytest.mark.parametrize("role,dept,team", [
        (Role.CTO_CEO, None, None),
        (Role.HR_MANAGER, None, None),
        (Role.DEPT_HEAD, 3, None),
        (Role.TEAM_LEAD, None, 7),
    ])
    def test_prefix_renders_for_all_roles(self, role, dept, team):
        from core.agent import _BASE_PREFIX, _UNRESTRICTED_RBAC, _RESTRICTED_RBAC
        ctx = make_ctx(role, dept_id=dept, team_id=team)

        if ctx.is_unrestricted:
            rbac_prefix = _UNRESTRICTED_RBAC
        else:
            scope_lines = ctx.scope_prompt().splitlines()
            scope_description = "\n".join(
                ln for ln in scope_lines if ln.startswith("DATA SCOPE")
            )
            rbac_prefix = _RESTRICTED_RBAC.format(
                role=ctx.role.value.upper().replace("_", " "),
                scope_description=scope_description,
            )

        from core.agent import _forbidden_columns_str
        prefix = _BASE_PREFIX.format(
            forbidden_columns=_forbidden_columns_str(),
            rbac_prefix=rbac_prefix,
            hr_records_note="",
        )
        assert len(prefix) > 100
        assert "PRIVACY" in prefix
        assert "salary" in prefix           # at least one forbidden column rendered
        assert "SELECT" in prefix


# ---------------------------------------------------------------------------
# sql_guard.rewrite_sql() — DB-layer scope enforcement
# ---------------------------------------------------------------------------

class TestSQLGuard:
    """
    Verify that rewrite_sql enforces scope at the SQL layer regardless of
    what the LLM was told in the prompt.  No database connection needed.
    """

    def setup_method(self):
        from core.rbac.sql_guard import rewrite_sql
        self.rewrite = rewrite_sql

    # --- unrestricted roles: no scope injection, SELECT allowed ---

    def test_unrestricted_no_scope_injected(self):
        ctx = make_ctx(Role.CTO_CEO)
        result = self.rewrite("SELECT * FROM person", ctx)
        assert "department_id" not in result
        assert "nsubteam_id" not in result
        assert "1 = 0" not in result

    def test_hr_manager_no_scope_injected(self):
        ctx = make_ctx(Role.HR_MANAGER)
        result = self.rewrite("SELECT full_name FROM person WHERE status_id = 10", ctx)
        assert "department_id" not in result
        assert "nsubteam_id" not in result
        assert "1 = 0" not in result

    # --- dept_head scope injection ---

    def test_dept_head_injects_department_filter(self):
        ctx = make_ctx(Role.DEPT_HEAD, dept_id=3)
        sql = "SELECT full_name FROM person WHERE status_id = 10"
        result = self.rewrite(sql, ctx)
        assert "department_id = 3" in result

    def test_dept_head_scope_defeats_or_injection(self):
        """WHERE (... OR 1=1) AND department_id=3 still restricts to dept 3."""
        ctx = make_ctx(Role.DEPT_HEAD, dept_id=3)
        injected_sql = "SELECT * FROM person WHERE department_id = 3 OR 1=1"
        result = self.rewrite(injected_sql, ctx)
        # Scope condition must appear AND'd after the injected conditions.
        assert "department_id = 3" in result
        # The original OR condition must be wrapped in parens.
        assert "(" in result

    def test_dept_head_no_existing_where(self):
        ctx = make_ctx(Role.DEPT_HEAD, dept_id=5)
        sql = "SELECT full_name FROM person"
        result = self.rewrite(sql, ctx)
        assert "department_id = 5" in result

    def test_dept_head_missing_dept_denies_all(self):
        ctx = make_ctx(Role.DEPT_HEAD, dept_id=None)
        result = self.rewrite("SELECT * FROM person", ctx)
        assert "1 = 0" in result

    def test_dept_head_non_person_table_still_scoped(self):
        # leave_record carries person_id — must be scoped even without a person join.
        ctx = make_ctx(Role.DEPT_HEAD, dept_id=3)
        sql = "SELECT COUNT(*) FROM leave_record WHERE status = 1"
        result = self.rewrite(sql, ctx)
        assert "department_id = 3" in result
        assert "person_id" in result

    def test_dept_head_no_scope_on_unlinked_table(self):
        # holiday_record has no person link → no scope injection.
        ctx = make_ctx(Role.DEPT_HEAD, dept_id=3)
        sql = "SELECT COUNT(*) FROM holiday_record WHERE is_active = true"
        result = self.rewrite(sql, ctx)
        assert "department_id" not in result

    def test_dept_head_different_dept_ids(self):
        sql = "SELECT * FROM person"
        r1 = self.rewrite(sql, make_ctx(Role.DEPT_HEAD, dept_id=1))
        r2 = self.rewrite(sql, make_ctx(Role.DEPT_HEAD, dept_id=2))
        assert "department_id = 1" in r1
        assert "department_id = 2" in r2
        assert r1 != r2

    # --- team_lead scope injection ---

    def test_team_lead_injects_person_team_subquery(self):
        ctx = make_ctx(Role.TEAM_LEAD, team_id=7)
        sql = "SELECT full_name FROM person WHERE status_id = 10"
        result = self.rewrite(sql, ctx)
        assert "nsubteam_id = 7" in result
        assert "person_team" in result
        assert "end_date IS NULL" in result

    def test_team_lead_scope_defeats_or_injection(self):
        ctx = make_ctx(Role.TEAM_LEAD, team_id=7)
        injected = "SELECT * FROM person WHERE 1=1"
        result = self.rewrite(injected, ctx)
        assert "nsubteam_id = 7" in result
        assert "(" in result  # original WHERE wrapped

    def test_team_lead_missing_team_denies_all(self):
        ctx = make_ctx(Role.TEAM_LEAD, team_id=None)
        result = self.rewrite("SELECT * FROM person", ctx)
        assert "1 = 0" in result

    # --- non-SELECT rejection (all roles, all statement types) ---

    @pytest.mark.parametrize("role,kwargs", [
        (Role.DEPT_HEAD,  {"dept_id": 3}),
        (Role.TEAM_LEAD,  {"team_id": 7}),
        (Role.HR_MANAGER, {}),
        (Role.CTO_CEO,    {}),
    ])
    @pytest.mark.parametrize("sql", [
        "INSERT INTO person (full_name) VALUES ('x')",
        "UPDATE person SET status_id = 11 WHERE id = 1",
        "DELETE FROM person WHERE id = 1",
        "DROP TABLE person",
        "TRUNCATE TABLE person",
        "ALTER TABLE person ADD COLUMN foo TEXT",
        "CREATE TABLE shadow AS SELECT * FROM person",
        "GRANT SELECT ON person TO attacker",
        "REVOKE SELECT ON person FROM hr_user",
    ])
    def test_non_select_blocked_for_all_roles(self, role, kwargs, sql):
        ctx = make_ctx(role, **kwargs)
        with pytest.raises(ValueError, match="Non-SELECT"):
            self.rewrite(sql, ctx)

    # --- table alias handling ---

    def test_dept_head_with_table_alias(self):
        ctx = make_ctx(Role.DEPT_HEAD, dept_id=3)
        sql = "SELECT p.full_name FROM person p WHERE p.status_id = 10"
        result = self.rewrite(sql, ctx)
        assert "department_id = 3" in result

    def test_team_lead_with_table_alias(self):
        ctx = make_ctx(Role.TEAM_LEAD, team_id=7)
        sql = "SELECT p.full_name FROM person p JOIN leave_record lr ON lr.person_id = p.id"
        result = self.rewrite(sql, ctx)
        assert "nsubteam_id = 7" in result


# ---------------------------------------------------------------------------
# Negative RBAC tests — boundary violations and bypass attempts
# ---------------------------------------------------------------------------

class TestNegativeRBAC:
    """
    Tests that verify access is DENIED or RESTRICTED in cases where it should
    be. Complement to the positive tests above.
    """

    def setup_method(self):
        from core.rbac.sql_guard import rewrite_sql
        self.rewrite = rewrite_sql

    # --- scope prompt must not grant unrestricted access to restricted roles ---

    def test_dept_head_prompt_not_full_access(self):
        prompt = make_ctx(Role.DEPT_HEAD, dept_id=3).scope_prompt()
        assert "full company-wide access" not in prompt

    def test_team_lead_prompt_not_full_access(self):
        prompt = make_ctx(Role.TEAM_LEAD, team_id=7).scope_prompt()
        assert "full company-wide access" not in prompt

    def test_dept_head_prompt_does_not_leak_other_dept(self):
        prompt = make_ctx(Role.DEPT_HEAD, dept_id=3).scope_prompt()
        assert "department_id = 4" not in prompt
        assert "department_id = 99" not in prompt

    def test_team_lead_prompt_does_not_leak_other_team(self):
        prompt = make_ctx(Role.TEAM_LEAD, team_id=7).scope_prompt()
        assert "nsubteam_id = 8" not in prompt
        assert "nsubteam_id = 99" not in prompt

    # --- can_see_employee: denied cases ---

    def test_dept_head_no_dept_id_cannot_see_anyone(self):
        ctx = make_ctx(Role.DEPT_HEAD, dept_id=None)
        assert ctx.can_see_employee(dept_id=1, team_id=None) is False

    def test_team_lead_no_team_id_cannot_see_anyone(self):
        ctx = make_ctx(Role.TEAM_LEAD, team_id=None)
        assert ctx.can_see_employee(dept_id=None, team_id=1) is False

    def test_dept_head_wrong_dept_denied(self):
        ctx = make_ctx(Role.DEPT_HEAD, dept_id=3)
        assert ctx.can_see_employee(dept_id=4, team_id=None) is False
        assert ctx.can_see_employee(dept_id=99, team_id=None) is False

    def test_team_lead_wrong_team_denied(self):
        ctx = make_ctx(Role.TEAM_LEAD, team_id=7)
        assert ctx.can_see_employee(dept_id=None, team_id=8) is False
        assert ctx.can_see_employee(dept_id=None, team_id=99) is False

    def test_dept_head_has_team_id_but_no_dept_id_denied(self):
        # Misconfigured: dept_head given team_id instead of dept_id.
        ctx = make_ctx(Role.DEPT_HEAD, dept_id=None, team_id=5)
        assert ctx.can_see_employee(dept_id=5, team_id=5) is False

    def test_team_lead_has_dept_id_but_no_team_id_denied(self):
        # Misconfigured: team_lead given dept_id instead of team_id.
        ctx = make_ctx(Role.TEAM_LEAD, dept_id=3, team_id=None)
        assert ctx.can_see_employee(dept_id=3, team_id=3) is False

    # --- strip_forbidden: must not redact non-forbidden words ---

    def test_partial_word_not_redacted(self):
        # "nic" inside "clinic" must not trigger forbidden-column redaction.
        ctx = make_ctx(Role.HR_MANAGER)
        text = "The clinic handled the case."
        assert ctx.strip_forbidden(text) == text

    def test_unrelated_text_passes_through_unchanged(self):
        ctx = make_ctx(Role.CTO_CEO)
        text = "Team velocity increased by 12% this sprint."
        assert ctx.strip_forbidden(text) == text

    # --- sql_guard: bypass attempts ---

    def test_dept_head_injected_wrong_dept_still_scoped_correctly(self):
        # LLM emits WHERE department_id = 99 — scope guard overrides with AND dept=3.
        ctx = make_ctx(Role.DEPT_HEAD, dept_id=3)
        result = self.rewrite("SELECT * FROM person WHERE department_id = 99", ctx)
        assert "department_id = 3" in result
        assert "(" in result  # injected condition wrapped in parens

    def test_union_both_branches_scoped(self):
        # UNION queries: both branches touching person must be scoped.
        ctx = make_ctx(Role.DEPT_HEAD, dept_id=3)
        sql = (
            "SELECT id FROM person WHERE status_id = 1 "
            "UNION ALL "
            "SELECT id FROM person WHERE status_id = 2"
        )
        result = self.rewrite(sql, ctx)
        assert result.count("department_id = 3") >= 2

    def test_subquery_person_reference_scoped(self):
        # Outer query on another table, inner subquery on person — guard must scope inner.
        ctx = make_ctx(Role.DEPT_HEAD, dept_id=3)
        sql = (
            "SELECT lr.id FROM leave_record lr "
            "WHERE lr.person_id IN (SELECT id FROM person WHERE 1=1)"
        )
        assert "department_id = 3" in self.rewrite(sql, ctx)

    def test_team_lead_union_both_branches_scoped(self):
        ctx = make_ctx(Role.TEAM_LEAD, team_id=7)
        sql = (
            "SELECT id FROM person WHERE status_id = 1 "
            "UNION ALL "
            "SELECT id FROM person WHERE status_id = 2"
        )
        assert self.rewrite(sql, ctx).count("nsubteam_id = 7") >= 2

    def test_none_rbac_ctx_select_allowed(self):
        # None ctx: SELECT passes through (no scope injection, no crash).
        result = self.rewrite("SELECT * FROM person", None)
        assert "department_id" not in result
        assert "nsubteam_id" not in result

    def test_none_rbac_ctx_non_select_blocked(self):
        # None ctx: non-SELECT still blocked — no role can write.
        with pytest.raises(ValueError, match="Non-SELECT"):
            self.rewrite("DELETE FROM person WHERE id = 1", None)


# ---------------------------------------------------------------------------
# sql_guard — person-linked tables scoped without a person join
# ---------------------------------------------------------------------------

class TestPersonLinkedTableScope:
    """
    Restricted roles must not read company-wide data through tables that
    carry employee data via person_id (or person_team_id) instead of a
    person join. Previously these queries bypassed the scope guard entirely.
    """

    def setup_method(self):
        from core.rbac.sql_guard import rewrite_sql
        self.rewrite = rewrite_sql

    @pytest.mark.parametrize("table", [
        "leave_record", "person_team", "person_week_log", "person_competency",
        "person_skill_category", "users_personresignation",
        "core_personstatushistory", "core_personemploymenthistory",
    ])
    def test_dept_head_person_fk_tables_scoped(self, table):
        ctx = make_ctx(Role.DEPT_HEAD, dept_id=3)
        result = self.rewrite(f"SELECT * FROM {table}", ctx)
        assert "department_id = 3" in result
        assert "person_id" in result

    def test_team_lead_person_fk_table_scoped(self):
        ctx = make_ctx(Role.TEAM_LEAD, team_id=7)
        result = self.rewrite("SELECT person_id, start FROM leave_record WHERE status = 1", ctx)
        assert "nsubteam_id = 7" in result

    def test_dept_head_person_team_fk_table_scoped(self):
        # person_week_project links via person_team_id, not person_id.
        ctx = make_ctx(Role.DEPT_HEAD, dept_id=3)
        result = self.rewrite("SELECT hours FROM person_week_project", ctx)
        assert "person_team_id" in result
        assert "department_id = 3" in result

    def test_dept_head_annual_review_scoped(self):
        ctx = make_ctx(Role.DEPT_HEAD, dept_id=3)
        result = self.rewrite("SELECT skill_rate FROM annual_review_response", ctx)
        assert "person_team_id" in result
        assert "department_id = 3" in result

    def test_person_join_present_scopes_person_only(self):
        # With a person join the person predicate constrains the linked table;
        # no redundant predicate (which would break LEFT JOIN semantics).
        ctx = make_ctx(Role.DEPT_HEAD, dept_id=3)
        sql = (
            "SELECT p.full_name, lr.start FROM person p "
            "LEFT JOIN leave_record lr ON lr.person_id = p.id"
        )
        result = self.rewrite(sql, ctx)
        assert result.count("department_id = 3") == 1

    def test_subquery_on_linked_table_scoped(self):
        # person in outer query, leave_record alone in subquery — inner scoped too.
        ctx = make_ctx(Role.DEPT_HEAD, dept_id=3)
        sql = (
            "SELECT full_name FROM person WHERE id IN "
            "(SELECT person_id FROM leave_record WHERE status = 1)"
        )
        result = self.rewrite(sql, ctx)
        assert result.count("department_id = 3") >= 2

    def test_dept_head_missing_dept_denies_linked_table(self):
        ctx = make_ctx(Role.DEPT_HEAD, dept_id=None)
        result = self.rewrite("SELECT * FROM leave_record", ctx)
        assert "1 = 0" in result

    def test_team_lead_missing_team_denies_linked_table(self):
        ctx = make_ctx(Role.TEAM_LEAD, team_id=None)
        result = self.rewrite("SELECT * FROM person_week_log", ctx)
        assert "1 = 0" in result

    def test_unrestricted_linked_tables_untouched(self):
        ctx = make_ctx(Role.HR_MANAGER)
        sql = "SELECT COUNT(*) FROM leave_record WHERE status = 1"
        result = self.rewrite(sql, ctx)
        assert "department_id" not in result
        assert "nsubteam_id" not in result

    def test_linked_table_with_alias_scoped(self):
        ctx = make_ctx(Role.DEPT_HEAD, dept_id=3)
        result = self.rewrite("SELECT lr.start FROM leave_record lr WHERE lr.status = 1", ctx)
        assert "department_id = 3" in result
        assert "lr." in result

    def test_union_linked_table_both_branches_scoped(self):
        ctx = make_ctx(Role.DEPT_HEAD, dept_id=3)
        sql = (
            "SELECT person_id FROM leave_record WHERE status = 1 "
            "UNION ALL "
            "SELECT person_id FROM person_week_log WHERE is_completed = false"
        )
        result = self.rewrite(sql, ctx)
        assert result.count("department_id = 3") >= 2


# ---------------------------------------------------------------------------
# sql_guard — forbidden columns blocked at the SQL layer (FR-5.8)
# ---------------------------------------------------------------------------

class TestForbiddenColumnsSQLGuard:
    """
    Forbidden columns (salary, NIC, DOB, …) must be blocked in the SQL layer
    for ALL roles — prompt-only enforcement is bypassable via prompt injection.
    """

    def setup_method(self):
        from core.rbac.sql_guard import rewrite_sql
        self.rewrite = rewrite_sql

    @pytest.mark.parametrize("ctx", [
        None,
        make_ctx(Role.CTO_CEO),
        make_ctx(Role.HR_MANAGER),
        make_ctx(Role.DEPT_HEAD, dept_id=3),
        make_ctx(Role.TEAM_LEAD, team_id=7),
    ])
    def test_forbidden_select_blocked_for_all_roles(self, ctx):
        with pytest.raises(ValueError, match="Forbidden column"):
            self.rewrite("SELECT salary FROM person", ctx)

    @pytest.mark.parametrize("col", [
        "salary", "gross_salary", "cnic", "date_of_birth", "dob",
        "bank_account", "personal_phone", "personal_email", "home_address",
    ])
    def test_each_forbidden_column_blocked(self, col):
        ctx = make_ctx(Role.HR_MANAGER)
        with pytest.raises(ValueError, match="Forbidden column"):
            self.rewrite(f"SELECT {col} FROM person", ctx)

    def test_forbidden_in_where_clause_blocked(self):
        # Filtering on salary leaks values via the result set even if not selected.
        ctx = make_ctx(Role.HR_MANAGER)
        with pytest.raises(ValueError, match="Forbidden column"):
            self.rewrite("SELECT full_name FROM person WHERE salary > 500000", ctx)

    def test_forbidden_in_order_by_blocked(self):
        ctx = make_ctx(Role.HR_MANAGER)
        with pytest.raises(ValueError, match="Forbidden column"):
            self.rewrite("SELECT full_name FROM person ORDER BY salary DESC", ctx)

    def test_forbidden_in_subquery_blocked(self):
        ctx = make_ctx(Role.HR_MANAGER)
        sql = (
            "SELECT full_name FROM person WHERE id IN "
            "(SELECT id FROM person WHERE salary > 100000)"
        )
        with pytest.raises(ValueError, match="Forbidden column"):
            self.rewrite(sql, ctx)

    def test_forbidden_in_aggregate_blocked(self):
        ctx = make_ctx(Role.CTO_CEO)
        with pytest.raises(ValueError, match="Forbidden column"):
            self.rewrite("SELECT AVG(salary) FROM person", ctx)

    def test_case_insensitive_blocked(self):
        ctx = make_ctx(Role.HR_MANAGER)
        with pytest.raises(ValueError, match="Forbidden column"):
            self.rewrite("SELECT SALARY FROM person", ctx)

    def test_clean_query_passes(self):
        ctx = make_ctx(Role.HR_MANAGER)
        result = self.rewrite("SELECT full_name, joining_date FROM person", ctx)
        assert "full_name" in result

    def test_similar_but_allowed_column_passes(self):
        # separation_date is allowed — must not be caught by substring logic.
        ctx = make_ctx(Role.HR_MANAGER)
        result = self.rewrite("SELECT full_name, separation_date FROM person", ctx)
        assert "separation_date" in result


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


# ---------------------------------------------------------------------------
# core.rate_limit.count_recent_queries — shared rate-limit counting
# ---------------------------------------------------------------------------

class TestCountRecentQueries:
    @staticmethod
    def make_engine():
        import sqlalchemy
        from core.rbac.models import Base
        engine = sqlalchemy.create_engine(
            "sqlite:///:memory:",
            poolclass=sqlalchemy.pool.StaticPool,
            connect_args={"check_same_thread": False},
        )
        Base.metadata.create_all(engine)
        return engine

    def test_counts_only_matching_user_within_last_hour(self):
        from datetime import datetime, timedelta, timezone
        from sqlalchemy.orm import Session
        from core.rate_limit import count_recent_queries
        from core.rbac.models import AuditLog

        engine = self.make_engine()
        now = datetime.now(timezone.utc)
        with Session(engine) as session:
            for _ in range(3):
                session.add(AuditLog(slack_user_id="U1", question="q", created_at=now))
            session.add(AuditLog(slack_user_id="U2", question="q", created_at=now))
            # Outside the 1-hour window — must not be counted.
            session.add(AuditLog(
                slack_user_id="U1", question="q", created_at=now - timedelta(hours=2),
            ))
            session.commit()

        assert count_recent_queries(engine, "U1") == 3
        assert count_recent_queries(engine, "U2") == 1
        assert count_recent_queries(engine, "U_UNKNOWN") == 0
