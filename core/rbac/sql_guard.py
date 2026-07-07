"""
SQL-layer scope enforcement for restricted RBAC roles.

Every SQL statement the LLM generates is passed through rewrite_sql()
before hitting the database.  This runs at the db.run() call site, so
it fires regardless of what the LLM was told in its prompt — prompt
injection cannot bypass it.

Rewriting rather than blocking is intentional: even if the LLM emits
  WHERE department_id = 3 OR 1=1
the rewrite produces
  WHERE (department_id = 3 OR 1=1) AND department_id = 3
which correctly restricts the result set to the user's department.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

import sqlglot
import sqlglot.expressions as exp

from core.rbac.context import FORBIDDEN_COLUMNS

if TYPE_CHECKING:
    from core.rbac.context import RBACContext

_BLOCKED_NODE_TYPES = (
    exp.Insert, exp.Update, exp.Delete,
    exp.Create, exp.Drop, exp.Alter, exp.TruncateTable,
)

# Tables that carry employee data via a direct person_id FK.  When one of
# these appears in a SELECT without a person join, the scope predicate is
# injected on the table itself — otherwise restricted roles could read
# company-wide data (e.g. SELECT * FROM leave_record) with no person
# reference for the guard to anchor on.
_PERSON_FK_TABLES = frozenset({
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
})

# Tables linked to a person indirectly through person_team_id.
_PERSON_TEAM_FK_TABLES = frozenset({
    "person_week_project",
    "annual_review_response",
})


def rewrite_sql(sql: str, rbac_ctx: Optional["RBACContext"]) -> str:
    """
    Parse sql, reject non-SELECT statements and forbidden-column references,
    then inject scope predicates into every SELECT node that references the
    person table or a person-linked table (person_id / person_team_id FK).

    Non-SELECT and forbidden-column blocking applies to ALL callers including
    None ctx and unrestricted roles — nobody may run INSERT/UPDATE/DELETE/DROP
    or read salary/NIC/DOB-class columns.  Scope injection is only applied for
    restricted roles (dept_head, team_lead).

    Returns the rewritten SQL string.  Raises ValueError on parse errors
    or non-SELECT statements (the LangChain agent surfaces these as tool
    observations and will retry with corrected SQL).
    """
    try:
        statements = sqlglot.parse(sql, read="postgres")
    except Exception as exc:
        raise ValueError(f"SQL parse error: {exc}") from exc

    restricted = rbac_ctx is not None and not rbac_ctx.is_unrestricted

    rewritten: list[str] = []
    for stmt in statements:
        if stmt is None:
            continue
        if not isinstance(stmt, (exp.Select, exp.Union, exp.Intersect, exp.Except, exp.With)):
            raise ValueError(
                f"Non-SELECT statement blocked by scope guard: {type(stmt).__name__}"
            )
        # CTEs can embed DML (e.g. WITH x AS (DELETE ... RETURNING ...) SELECT ...).
        # sqlglot parses these as exp.With, passing the isinstance check above, so
        # we must also walk the tree and reject any DML/DDL node found anywhere.
        for bad in stmt.find_all(*_BLOCKED_NODE_TYPES):
            raise ValueError(
                f"Non-SELECT statement blocked by scope guard: {type(bad).__name__}"
            )
        # Forbidden columns (FR-5.8) are blocked for ALL roles at the SQL layer.
        # Any reference counts — SELECT list, WHERE, ORDER BY, aggregates —
        # since even filtering on salary leaks values via the result set.
        for column in stmt.find_all(exp.Column):
            if column.name and column.name.lower() in FORBIDDEN_COLUMNS:
                raise ValueError(
                    f"Forbidden column blocked by scope guard: {column.name}"
                )
        # Wildcard projections (SELECT *, SELECT p.*) would bypass the check
        # above — sqlglot represents * as exp.Star, not exp.Column — and could
        # return forbidden columns.  Reject them so the agent must enumerate
        # columns explicitly.  COUNT(*) is allowed: it returns no column data.
        for star in stmt.find_all(exp.Star):
            parent = star.parent
            if isinstance(parent, exp.Column):  # qualified star, e.g. p.*
                parent = parent.parent
            if isinstance(parent, exp.Count):
                continue
            raise ValueError(
                "Wildcard projection blocked by scope guard: "
                "SELECT the specific columns you need (COUNT(*) is allowed)."
            )
        if restricted:
            _inject_scope_into_tree(stmt, rbac_ctx)
        rewritten.append(stmt.sql(dialect="postgres"))

    return "; ".join(rewritten)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _inject_scope_into_tree(tree: exp.Expression, rbac_ctx: "RBACContext") -> None:
    # Materialise before mutating — injected predicates contain their own
    # SELECT subqueries, which a live find_all() generator would re-visit
    # and re-scope.
    for select in list(tree.find_all(exp.Select)):
        alias = _person_alias(select)
        if alias is not None:
            # person is present — its predicate constrains every joined
            # person-linked table, so scope person only.  Also injecting on
            # linked tables would break LEFT JOIN semantics for legit queries.
            scope_sql = _scope_sql(rbac_ctx, alias)
            if scope_sql:
                _inject_and(select, scope_sql)
            continue
        # No person reference — scope each person-linked table directly.
        for table in _select_tables(select):
            name = table.name.lower()
            if name in _PERSON_FK_TABLES:
                _inject_and(select, _fk_scope_sql(rbac_ctx, table.alias_or_name, "person_id"))
            elif name in _PERSON_TEAM_FK_TABLES:
                _inject_and(select, _person_team_fk_scope_sql(rbac_ctx, table.alias_or_name))


def _select_tables(select: exp.Select) -> list[exp.Table]:
    """Immediate FROM/JOIN table references of this SELECT — not descendants."""
    tables: list[exp.Table] = []
    from_clause = select.args.get("from_")
    if from_clause and isinstance(from_clause.this, exp.Table):
        tables.append(from_clause.this)
    for join in select.args.get("joins", []) or []:
        if isinstance(join.this, exp.Table):
            tables.append(join.this)
    return tables


def _person_alias(select: exp.Select) -> Optional[str]:
    """Return the alias (or bare name) used for the person table in this SELECT.

    Only inspects the immediate FROM/JOIN table references — not descendants.
    Using find_all() would recurse into subqueries and detect person tables that
    belong to an inner scope, causing the outer WHERE injection to reference an
    alias that doesn't exist at that level.
    """
    for table in _select_tables(select):
        if table.name.lower() == "person":
            return table.alias_or_name
    return None


def _scope_sql(rbac_ctx: "RBACContext", person_alias: str) -> Optional[str]:
    role = rbac_ctx.role.value

    if role == "dept_head":
        if rbac_ctx.department_id is None:
            return "1 = 0"
        dept_id = int(rbac_ctx.department_id)
        return f"{person_alias}.department_id = {dept_id}"

    if role == "team_lead":
        if rbac_ctx.team_id is None:
            return "1 = 0"
        team_id = int(rbac_ctx.team_id)
        return (
            f"{person_alias}.id IN ("
            f"SELECT person_id FROM person_team "
            f"WHERE nsubteam_id = {team_id} "
            f"AND end_date IS NULL AND is_active = true"
            f")"
        )

    # Unknown restricted role — deny all person data.
    return "1 = 0"


def _scoped_person_ids_sql(rbac_ctx: "RBACContext") -> Optional[str]:
    """Subquery yielding the person ids visible to this restricted role,
    or None when the role is misconfigured (caller must deny all)."""
    role = rbac_ctx.role.value

    if role == "dept_head":
        if rbac_ctx.department_id is None:
            return None
        return f"SELECT id FROM person WHERE department_id = {int(rbac_ctx.department_id)}"

    if role == "team_lead":
        if rbac_ctx.team_id is None:
            return None
        return (
            f"SELECT person_id FROM person_team "
            f"WHERE nsubteam_id = {int(rbac_ctx.team_id)} "
            f"AND end_date IS NULL AND is_active = true"
        )

    return None


def _fk_scope_sql(rbac_ctx: "RBACContext", alias: str, fk_column: str) -> str:
    person_ids = _scoped_person_ids_sql(rbac_ctx)
    if person_ids is None:
        return "1 = 0"
    return f"{alias}.{fk_column} IN ({person_ids})"


def _person_team_fk_scope_sql(rbac_ctx: "RBACContext", alias: str) -> str:
    person_ids = _scoped_person_ids_sql(rbac_ctx)
    if person_ids is None:
        return "1 = 0"
    return (
        f"{alias}.person_team_id IN ("
        f"SELECT id FROM person_team WHERE person_id IN ({person_ids})"
        f")"
    )


def _inject_and(select: exp.Select, scope_sql: str) -> None:
    """
    Append scope_sql as an AND condition to select's WHERE clause.

    Existing conditions are wrapped in parentheses so that any OR-based
    bypass in the LLM-generated SQL cannot escape the scope restriction.
    """
    scope_expr = sqlglot.parse_one(scope_sql, read="postgres")
    existing_where = select.args.get("where")

    if existing_where:
        new_cond = exp.And(
            this=exp.Paren(this=existing_where.this),
            expression=scope_expr,
        )
    else:
        new_cond = scope_expr

    select.set("where", exp.Where(this=new_cond))
