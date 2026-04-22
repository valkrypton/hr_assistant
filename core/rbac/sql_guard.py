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

if TYPE_CHECKING:
    from core.rbac.context import RBACContext


def rewrite_sql(sql: str, rbac_ctx: "RBACContext") -> str:
    """
    Parse sql, reject non-SELECT statements, then inject scope predicates
    into every SELECT node that references the person table.

    Non-SELECT blocking applies to ALL callers including None ctx and
    unrestricted roles — nobody may run INSERT/UPDATE/DELETE/DROP.
    Scope injection is only applied for restricted roles (dept_head,
    team_lead).

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
        if restricted:
            _inject_scope_into_tree(stmt, rbac_ctx)
        rewritten.append(stmt.sql(dialect="postgres"))

    return "; ".join(rewritten)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _inject_scope_into_tree(tree: exp.Expression, rbac_ctx: "RBACContext") -> None:
    for select in tree.find_all(exp.Select):
        alias = _person_alias(select)
        if alias is None:
            continue
        scope_sql = _scope_sql(rbac_ctx, alias)
        if scope_sql:
            _inject_and(select, scope_sql)


def _person_alias(select: exp.Select) -> Optional[str]:
    """Return the alias (or bare name) used for the person table in this SELECT."""
    # sqlglot stores the FROM clause under "from_" (trailing underscore avoids
    # shadowing the Python built-in).
    from_clause = select.args.get("from_")
    if from_clause:
        for table in from_clause.find_all(exp.Table):
            if table.name.lower() == "person":
                return table.alias_or_name
    for join in select.args.get("joins", []) or []:
        for table in join.find_all(exp.Table):
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
            f"AND end_date IS NULL AND is_active = 1"
            f")"
        )

    # Unknown restricted role — deny all person data.
    return "1 = 0"


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
