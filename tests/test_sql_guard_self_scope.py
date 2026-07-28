"""Self-scope predicate injection — the enforcement boundary."""

import pytest

from core.rbac.access import AccessLevel
from core.rbac.context import RBACContext
from core.rbac.sql_guard import rewrite_sql

SELF = RBACContext(access_level=AccessLevel.SELF, person_id=42)
OPEN = RBACContext.unrestricted()


def test_person_table_scoped_to_own_id():
    out = rewrite_sql("SELECT full_name FROM person", SELF)
    assert "person.id = 42" in out.replace('"', "")


def test_person_alias_respected():
    out = rewrite_sql("SELECT p.full_name FROM person p", SELF)
    assert "p.id = 42" in out.replace('"', "")


def test_person_fk_table_scoped():
    out = rewrite_sql("SELECT start_date FROM leave_record", SELF)
    assert "leave_record.person_id = 42" in out.replace('"', "")


def test_person_team_fk_table_scoped():
    out = rewrite_sql("SELECT hours FROM person_week_project", SELF)
    normalised = out.replace('"', "")
    assert "person_team_id IN (SELECT id FROM person_team WHERE person_id = 42)" in normalised


def test_person_free_table_not_scoped():
    out = rewrite_sql("SELECT name FROM department", SELF)
    assert "WHERE" not in out.upper()


def test_unclassified_table_fails_closed():
    with pytest.raises(ValueError, match="not classified"):
        rewrite_sql("SELECT id FROM auth_user", SELF)


def test_or_injection_cannot_escape_scope():
    """The paren-wrapping in _inject_and is what makes this hold."""
    out = rewrite_sql("SELECT full_name FROM person WHERE id = 1 OR 1 = 1", SELF)
    normalised = out.replace('"', "")
    assert "(id = 1 OR 1 = 1) AND person.id = 42" in normalised


def test_missing_person_id_denies_all():
    ctx = RBACContext(access_level=AccessLevel.SELF, person_id=None)
    out = rewrite_sql("SELECT full_name FROM person", ctx)
    assert "1 = 0" in out


def test_unrestricted_gets_no_injection():
    out = rewrite_sql("SELECT full_name FROM person", OPEN)
    assert "WHERE" not in out.upper()


def test_join_scopes_both_tables():
    out = rewrite_sql(
        "SELECT p.full_name, l.start_date FROM person p JOIN leave_record l ON l.person_id = p.id",
        SELF,
    )
    normalised = out.replace('"', "")
    assert "p.id = 42" in normalised
    assert "l.person_id = 42" in normalised


def test_forbidden_column_still_blocked_for_unrestricted():
    with pytest.raises(ValueError, match="Forbidden column"):
        rewrite_sql("SELECT salary FROM person", OPEN)


def test_dml_still_blocked_for_unrestricted():
    with pytest.raises(ValueError, match="Non-SELECT"):
        rewrite_sql("DELETE FROM person", OPEN)


# ---------------------------------------------------------------------------
# Additional coverage ported from the old role-based tests/test_rbac.py,
# adapted to the two-level access model. Not duplicated above: UNION/subquery
# scoping, the full _PERSON_FK_TABLES sweep, exhaustive forbidden-column
# placements, wildcard projections, and whole-row references.
# ---------------------------------------------------------------------------


def test_union_both_branches_scoped():
    sql = (
        "SELECT id FROM person WHERE status_id = 1 "
        "UNION ALL "
        "SELECT id FROM person WHERE status_id = 2"
    )
    result = rewrite_sql(sql, SELF)
    assert result.count("person.id = 42") >= 2


def test_subquery_person_reference_scoped():
    sql = (
        "SELECT lr.id FROM leave_record lr WHERE lr.person_id IN (SELECT id FROM person WHERE 1=1)"
    )
    result = rewrite_sql(sql, SELF)
    assert "person.id = 42" in result.replace('"', "")


def test_left_join_scopes_person_and_fk_table_independently():
    sql = (
        "SELECT p.full_name, lr.start_date FROM person p "
        "LEFT JOIN leave_record lr ON lr.person_id = p.id"
    )
    result = rewrite_sql(sql, SELF)
    normalised = result.replace('"', "")
    assert "p.id = 42" in normalised
    assert "lr.person_id = 42" in normalised


def test_cross_join_person_does_not_leak_fk_table():
    """A CROSS JOIN to person does not constrain leave_record — both tables
    must still receive their own scope predicate."""
    sql = "SELECT lr.person_id FROM leave_record lr CROSS JOIN person p"
    result = rewrite_sql(sql, SELF)
    normalised = result.replace('"', "")
    assert "p.id = 42" in normalised
    assert "lr.person_id = 42" in normalised


@pytest.mark.parametrize(
    "table",
    [
        "person_team",
        "leave_record",
        "person_week_log",
        "person_competency",
        "person_skill_category",
        "users_personresignation",
        "core_personstatushistory",
        "core_personemploymenthistory",
        "core_personemploymenttypehistory",
        "person_leave_limit",
        "job_requisition",
    ],
)
def test_every_person_fk_table_scoped(table):
    result = rewrite_sql(f"SELECT id FROM {table}", SELF)
    assert f"{table}.person_id = 42" in result.replace('"', "")


def test_unrestricted_linked_tables_untouched():
    result = rewrite_sql("SELECT COUNT(*) FROM leave_record WHERE status = 1", OPEN)
    assert "person_id" not in result
    assert result == "SELECT COUNT(*) FROM leave_record WHERE status = 1"


@pytest.mark.parametrize("ctx", [None, SELF, OPEN])
@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO person (full_name) VALUES ('x')",
        "UPDATE person SET status_id = 11 WHERE id = 1",
        "DELETE FROM person WHERE id = 1",
        "DROP TABLE person",
        "TRUNCATE TABLE person",
        "ALTER TABLE person ADD COLUMN foo TEXT",
        "CREATE TABLE shadow AS SELECT * FROM person",
        "GRANT SELECT ON person TO attacker",
        "REVOKE SELECT ON person FROM hr_user",
    ],
)
def test_non_select_blocked_for_every_access_level(ctx, sql):
    with pytest.raises(ValueError, match="Non-SELECT"):
        rewrite_sql(sql, ctx)


def test_none_ctx_select_allowed():
    result = rewrite_sql("SELECT id FROM person", None)
    assert "WHERE" not in result.upper()


class TestForbiddenColumnsExhaustive:
    """Forbidden columns must be blocked at the SQL layer for every access
    level and in every clause position — prompt-only enforcement is
    bypassable via prompt injection."""

    @pytest.mark.parametrize("ctx", [None, SELF, OPEN])
    def test_forbidden_select_blocked(self, ctx):
        with pytest.raises(ValueError, match="Forbidden column"):
            rewrite_sql("SELECT salary FROM person", ctx)

    @pytest.mark.parametrize(
        "col",
        [
            "salary",
            "gross_salary",
            "cnic",
            "date_of_birth",
            "dob",
            "bank_account",
            "personal_phone",
            "personal_email",
            "home_address",
        ],
    )
    def test_each_forbidden_column_blocked(self, col):
        with pytest.raises(ValueError, match="Forbidden column"):
            rewrite_sql(f"SELECT {col} FROM person", OPEN)

    def test_forbidden_in_where_clause_blocked(self):
        with pytest.raises(ValueError, match="Forbidden column"):
            rewrite_sql("SELECT full_name FROM person WHERE salary > 500000", OPEN)

    def test_forbidden_in_order_by_blocked(self):
        with pytest.raises(ValueError, match="Forbidden column"):
            rewrite_sql("SELECT full_name FROM person ORDER BY salary DESC", OPEN)

    def test_forbidden_in_subquery_blocked(self):
        sql = (
            "SELECT full_name FROM person WHERE id IN (SELECT id FROM person WHERE salary > 100000)"
        )
        with pytest.raises(ValueError, match="Forbidden column"):
            rewrite_sql(sql, OPEN)

    def test_forbidden_in_aggregate_blocked(self):
        with pytest.raises(ValueError, match="Forbidden column"):
            rewrite_sql("SELECT AVG(salary) FROM person", OPEN)

    def test_case_insensitive_blocked(self):
        with pytest.raises(ValueError, match="Forbidden column"):
            rewrite_sql("SELECT SALARY FROM person", OPEN)

    def test_similar_but_allowed_column_passes(self):
        # separation_date is allowed — must not be caught by substring logic.
        result = rewrite_sql("SELECT full_name, separation_date FROM person", OPEN)
        assert "separation_date" in result


class TestWildcardProjectionGuard:
    """SELECT * (and qualified variants like p.*) must be rejected for every
    access level: sqlglot represents * as exp.Star, not exp.Column, so it
    would otherwise bypass the forbidden-column check and smuggle forbidden
    columns past the guard. COUNT(*) is exempt since it returns no column
    data."""

    @pytest.mark.parametrize("ctx", [None, SELF, OPEN])
    def test_select_star_blocked(self, ctx):
        with pytest.raises(ValueError, match="Wildcard"):
            rewrite_sql("SELECT * FROM person", ctx)

    def test_qualified_star_blocked(self):
        with pytest.raises(ValueError, match="Wildcard"):
            rewrite_sql("SELECT p.* FROM person p", OPEN)

    def test_star_in_exists_subquery_allowed(self):
        sql = "SELECT full_name FROM person WHERE EXISTS (SELECT * FROM leave_record)"
        result = rewrite_sql(sql, OPEN)
        assert "EXISTS" in result

    def test_count_star_allowed_and_scoped_for_self(self):
        result = rewrite_sql("SELECT COUNT(*) FROM leave_record", SELF)
        assert "COUNT(*)" in result
        assert "person_id = 42" in result.replace('"', "")


class TestWholeRowReferenceGuard:
    """A bare, unqualified identifier matching a table alias (SELECT p,
    to_jsonb(p)) is a whole-row reference — sqlglot parses it as a Column
    named after the alias, which would otherwise smuggle every column,
    including forbidden ones, past the forbidden-column check."""

    @pytest.mark.parametrize("ctx", [None, SELF, OPEN])
    def test_bare_alias_select_blocked(self, ctx):
        with pytest.raises(ValueError, match="Whole-row reference"):
            rewrite_sql("SELECT p FROM person p", ctx)

    @pytest.mark.parametrize("ctx", [None, SELF, OPEN])
    def test_to_jsonb_of_alias_blocked(self, ctx):
        with pytest.raises(ValueError, match="Whole-row reference"):
            rewrite_sql("SELECT to_jsonb(p) FROM person p", ctx)

    def test_qualified_column_not_treated_as_whole_row(self):
        result = rewrite_sql("SELECT p.full_name FROM person p", SELF)
        assert "p.id = 42" in result.replace('"', "")


class TestUnclassifiedTableGuard:
    def test_person_free_lookup_table_allowed_no_injection(self):
        result = rewrite_sql("SELECT name FROM department", SELF)
        assert "WHERE" not in result.upper()

    def test_unclassified_table_allowed_for_unrestricted(self):
        # Fail-closed only applies to SELF — UNRESTRICTED never goes through
        # scope injection, so an unclassified table passes.
        result = rewrite_sql("SELECT x FROM person_bonus", OPEN)
        assert "x" in result
