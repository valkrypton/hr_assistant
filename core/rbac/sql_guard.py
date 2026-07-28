"""
SQL-layer scope enforcement for AccessLevel.SELF requesters.

Every SQL statement the LLM generates is passed through rewrite_sql()
before hitting the database.  This runs at the db.run() call site, so
it fires regardless of what the LLM was told in its prompt — prompt
injection cannot bypass it.

Rewriting rather than blocking is intentional: even if the LLM emits
  WHERE id = 1 OR 1=1
the rewrite produces
  WHERE (id = 1 OR 1=1) AND person.id = 42
which correctly restricts the result set to the requester's own row.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlglot
import sqlglot.expressions as exp

from core.rbac.context import FORBIDDEN_COLUMNS

if TYPE_CHECKING:
    from core.rbac.context import RBACContext

_BLOCKED_NODE_TYPES = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Create,
    exp.Drop,
    exp.Alter,
    exp.TruncateTable,
)

# PostgreSQL functions that execute a SQL string with the caller's privileges
# (bypassing scope + forbidden-column checks, since the inner SQL is an opaque
# string literal the parser never inspects), read the filesystem/network, or
# enable denial of service. sqlglot parses all of these as exp.Anonymous, so a
# name denylist over Anonymous nodes catches them without touching legitimate
# typed functions (COUNT, AVG, DATE_TRUNC, ...). This is DEFENSE IN DEPTH and is
# necessarily incomplete — Postgres/extensions keep adding functions. The
# PRIMARY control must be a least-privilege read-only ERP DB role with EXECUTE
# revoked on these; the denylist is a backstop, not the boundary.
_BLOCKED_FUNCTIONS = frozenset(
    {
        # execute a SQL string with caller privileges
        "query_to_xml",
        "query_to_xmlschema",
        "query_to_xml_and_xmlschema",
        "dblink",
        "dblink_exec",
        "dblink_open",
        "dblink_fetch",
        "dblink_connect",
        "dblink_send_query",
        "dblink_get_result",
        # filesystem / large-object access
        "pg_read_file",
        "pg_read_binary_file",
        "pg_ls_dir",
        "pg_ls_logdir",
        "pg_ls_waldir",
        "pg_ls_tmpdir",
        "pg_ls_archive_statusdir",
        "pg_ls_logicalsnapdir",
        "pg_ls_logicalmapdir",
        "pg_ls_replslotdir",
        "pg_stat_file",
        "pg_file_write",
        "lo_import",
        "lo_export",
        "lo_get",
        "lo_put",
        # denial of service
        "pg_sleep",
        "pg_sleep_for",
        "pg_sleep_until",
        "pg_terminate_backend",
        "pg_cancel_backend",
    }
)

# Tables that carry employee data via a direct person_id FK.  When one of
# these appears in a SELECT without a person join, the scope predicate is
# injected on the table itself — otherwise restricted roles could read
# company-wide data (e.g. SELECT * FROM leave_record) with no person
# reference for the guard to anchor on.
_PERSON_FK_TABLES = frozenset(
    {
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
    }
)

# Tables linked to a person indirectly through person_team_id.
_PERSON_TEAM_FK_TABLES = frozenset(
    {
        "person_week_project",
        "annual_review_response",
    }
)

# Lookup/reference tables that hold no per-person employee data, so restricted
# roles may read them company-wide (department names, leave types, holidays,
# competency dimensions, etc.).  Any table that is neither `person`, nor
# person-linked (the two sets above), nor listed here is treated as
# unclassified: for restricted roles the guard fails closed rather than risk
# leaking an unscoped person-bearing table added to INCLUDED_TABLES later.
_PERSON_FREE_TABLES = frozenset(
    {
        "department",
        "team",
        "designation",
        "employment_type",
        "leave_type",
        "leave_limit",
        "holiday_record",
        "competency_role",
        "competency",
        "competency_level",
        "skill_category",
        # No FK to person or any person-linked table — confirmed with the
        # team while closing the "mis-listed table" gap (2026-07-21).
        "available_time",
    }
)

_ALL_CLASSIFIED_TABLES = (
    frozenset({"person"}) | _PERSON_FK_TABLES | _PERSON_TEAM_FK_TABLES | _PERSON_FREE_TABLES
)

# Explicit, product-approved exceptions to the person-free FK check below —
# a (table, column) pair that DOES reference person but was deliberately
# judged not to need per-request RBAC scoping. NOT a blanket escape hatch:
# every entry here must be justified in docs/rbac-classification-gaps.md, and
# adding one should be as deliberate as adding a table to _PERSON_FREE_TABLES
# itself.
#
# team.lead_id: which person leads a team is treated as org-chart metadata,
# the same category as department names — not on the forbidden-columns list
# (salary/CNIC/DOB/etc.) and not "whose record is this" employee data — so
# it stays visible company-wide rather than scoped to the requester's own
# department/team. Confirmed as a product decision, not a default I picked.
_APPROVED_PERSON_FK_EXCEPTIONS = frozenset(
    {
        ("team", "lead_id"),
    }
)


def assert_tables_classified(included_tables: list[str]) -> None:
    """
    Defense-in-depth against an *unlisted* table: fail closed at startup,
    not just at query time. rewrite_sql already rejects an unclassified
    table for a restricted role's actual query — this catches the same gap
    at deploy time instead, before any request is ever served, by comparing
    the deployed INCLUDED_TABLES against the three classification sets above.
    """
    unclassified = sorted(set(included_tables) - _ALL_CLASSIFIED_TABLES)
    if unclassified:
        raise RuntimeError(
            f"INCLUDED_TABLES contains table(s) not classified in sql_guard's "
            f"scope-enforcement sets: {', '.join(unclassified)}. Add each to "
            "_PERSON_FK_TABLES, _PERSON_TEAM_FK_TABLES, or _PERSON_FREE_TABLES "
            "in core/rbac/sql_guard.py before a restricted role can query it."
        )


def assert_person_free_tables_have_no_person_fk(metadata, included_tables: list[str]) -> None:
    """
    Defense-in-depth against a *mis*-listed table: assert_tables_classified
    only catches a table missing from all three sets — a table wrongly
    placed IN _PERSON_FREE_TABLES is still "classified" and would pass that
    check while leaking person-scoped data to every restricted role.

    `metadata` is a reflected sqlalchemy.MetaData (SQLDatabase.from_uri
    already reflects one) — walk each _PERSON_FREE_TABLES member actually in
    use and fail closed if it carries a person_id column or an FK into
    `person`, unless that specific (table, column) is an explicit,
    documented exception (_APPROVED_PERSON_FK_EXCEPTIONS above).
    """
    suspects = []
    for name in sorted(set(included_tables) & _PERSON_FREE_TABLES):
        table = metadata.tables.get(name)
        if table is None:
            continue
        for col in table.columns:
            if (name, col.name) in _APPROVED_PERSON_FK_EXCEPTIONS:
                continue
            has_person_fk = any(fk.column.table.name == "person" for fk in col.foreign_keys)
            if col.name == "person_id" or has_person_fk:
                suspects.append(name)
                break
    if suspects:
        raise RuntimeError(
            f"Table(s) classified as person-free in sql_guard._PERSON_FREE_TABLES "
            f"actually carry a person_id column or a foreign key into person: "
            f"{', '.join(suspects)}. Reclassify into _PERSON_FK_TABLES (or "
            "_PERSON_TEAM_FK_TABLES) before a restricted role can query it."
        )


def rewrite_sql(sql: str, rbac_ctx: RBACContext | None) -> str:
    """
    Parse sql, reject non-SELECT statements and forbidden-column references,
    then inject scope predicates into every SELECT node that references the
    person table or a person-linked table (person_id / person_team_id FK).

    Non-SELECT and forbidden-column blocking applies to ALL callers including
    None ctx and unrestricted access levels — nobody may run
    INSERT/UPDATE/DELETE/DROP or read salary/NIC/DOB-class columns.  Scope
    injection is only applied for AccessLevel.SELF requesters; UNRESTRICTED
    (HR / Management group members) get no row predicates.

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
            raise ValueError(f"Non-SELECT statement blocked by scope guard: {type(stmt).__name__}")
        # CTEs can embed DML (e.g. WITH x AS (DELETE ... RETURNING ...) SELECT ...).
        # sqlglot parses these as exp.With, passing the isinstance check above, so
        # we must also walk the tree and reject any DML/DDL node found anywhere.
        for bad in stmt.find_all(*_BLOCKED_NODE_TYPES):
            raise ValueError(f"Non-SELECT statement blocked by scope guard: {type(bad).__name__}")
        # SELECT ... INTO writes a new table. sqlglot parses it as a Select
        # carrying an exp.Into child, so it slips past the isinstance gate above
        # and the DML/DDL walk. Reject it explicitly.
        if stmt.find(exp.Into) is not None:
            raise ValueError("SELECT ... INTO blocked by scope guard: it writes a table.")
        # SQL-executing / filesystem / DoS functions (see _BLOCKED_FUNCTIONS).
        # Blocked for ALL roles — their string arguments bypass every other
        # check in this guard.
        for func in stmt.find_all(exp.Anonymous):
            fname = (func.name or "").lower()
            if fname in _BLOCKED_FUNCTIONS:
                raise ValueError(f"Function blocked by scope guard: {func.name}")
        # All table names/aliases in the statement — used to detect whole-row
        # references below.
        table_names = set()
        for tbl in stmt.find_all(exp.Table):
            table_names.add(tbl.alias_or_name.lower())
            table_names.add(tbl.name.lower())
        # Forbidden columns (FR-5.8) are blocked for ALL roles at the SQL layer.
        # Any reference counts — SELECT list, WHERE, ORDER BY, aggregates —
        # since even filtering on salary leaks values via the result set.
        # A bare, unqualified identifier that matches a table alias is a
        # whole-row reference (SELECT p, to_jsonb(p)); sqlglot parses it as a
        # Column named after the alias, so it would otherwise smuggle every
        # column — including forbidden ones — past this check.
        for column in stmt.find_all(exp.Column):
            name = (column.name or "").lower()
            if name in FORBIDDEN_COLUMNS:
                raise ValueError(f"Forbidden column blocked by scope guard: {column.name}")
            if not column.table and name in table_names:
                raise ValueError(
                    f"Whole-row reference blocked by scope guard: '{column.name}'. "
                    "SELECT the specific columns you need."
                )
        # Wildcard projections (SELECT *, SELECT p.*) would bypass the check
        # above — sqlglot represents * as exp.Star, not exp.Column — and could
        # return forbidden columns.  Reject them so the agent must enumerate
        # columns explicitly.  COUNT(*) and EXISTS (SELECT * ...) are allowed:
        # neither returns column data.
        for star in stmt.find_all(exp.Star):
            parent = star.parent
            if isinstance(parent, exp.Column):  # qualified star, e.g. p.*
                parent = parent.parent
            if isinstance(parent, exp.Count):
                continue
            if star.find_ancestor(exp.Exists) is not None:
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


def _inject_scope_into_tree(tree: exp.Expression, rbac_ctx: RBACContext) -> None:
    # Materialise before mutating — injected predicates contain their own
    # SELECT subqueries, which a live find_all() generator would re-visit
    # and re-scope.
    for select in list(tree.find_all(exp.Select)):
        # Scope the person table itself if present.
        alias = _person_alias(select)
        if alias is not None:
            _inject_and(select, _scope_sql(rbac_ctx, alias))
        # Independently scope every person-linked table in this SELECT,
        # regardless of whether person is also present.  A spurious or
        # cartesian join to person (CROSS JOIN person, JOIN person ON 1=1)
        # does not constrain these tables, so relying on person's predicate
        # alone would leave them unscoped.
        for table in _select_tables(select):
            name = table.name.lower()
            if name == "person":
                continue  # already scoped above
            if name in _PERSON_FK_TABLES:
                _inject_and(select, _fk_scope_sql(rbac_ctx, table.alias_or_name, "person_id"))
            elif name in _PERSON_TEAM_FK_TABLES:
                _inject_and(select, _person_team_fk_scope_sql(rbac_ctx, table.alias_or_name))
            elif name not in _PERSON_FREE_TABLES:
                # Unclassified table under a restricted role — fail closed
                # rather than risk leaking an unscoped person-bearing table.
                raise ValueError(
                    f"Table '{name}' is not classified for scope enforcement; "
                    "it must be registered as person-linked or person-free in "
                    "sql_guard before a restricted role can query it."
                )


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


def _person_alias(select: exp.Select) -> str | None:
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


def _self_person_id(rbac_ctx: RBACContext) -> int | None:
    """The single person id a restricted requester may see, or None when the
    context is misconfigured (caller must then deny all)."""
    person_id = rbac_ctx.person_id
    return int(person_id) if person_id is not None else None


def _scope_sql(rbac_ctx: RBACContext, person_alias: str) -> str:
    person_id = _self_person_id(rbac_ctx)
    if person_id is None:
        return "1 = 0"
    return f"{person_alias}.id = {person_id}"


def _fk_scope_sql(rbac_ctx: RBACContext, alias: str, fk_column: str) -> str:
    person_id = _self_person_id(rbac_ctx)
    if person_id is None:
        return "1 = 0"
    return f"{alias}.{fk_column} = {person_id}"


def _person_team_fk_scope_sql(rbac_ctx: RBACContext, alias: str) -> str:
    person_id = _self_person_id(rbac_ctx)
    if person_id is None:
        return "1 = 0"
    return f"{alias}.person_team_id IN (SELECT id FROM person_team WHERE person_id = {person_id})"


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
