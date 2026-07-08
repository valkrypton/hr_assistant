"""Guarded free-form SQL tool.

The agent's escape hatch for query shapes not covered by a typed tool. Every
statement passes through core.rbac.sql_guard.rewrite_sql (blocks non-SELECT,
forbidden columns, dangerous functions; injects scope predicates for restricted
roles) before executing on the read-only ERP engine. A guard rejection is
returned as an "Error: ..." observation — not raised — so a tool-calling agent
can rewrite and retry (the contract the legacy agent relied on).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import sqlalchemy
import sqlglot
import sqlglot.expressions as exp
from pydantic import BaseModel, Field

from core.db import erp_engine
from core.rbac.sql_guard import rewrite_sql
from core.tools.base import Tool


@dataclass
class SqlCollector:
    """Accumulates executed SQL + accessed tables + row counts across the tool
    calls of a single agent run, for the audit log (populated in PR9)."""

    statements: list[str] = field(default_factory=list)
    tables: set[str] = field(default_factory=set)
    rows_returned: int = 0

    def record(self, sql: str, tables: set[str], rows: int) -> None:
        self.statements.append(sql)
        self.tables.update(tables)
        self.rows_returned += rows


def _tables_in_sql(sql: str) -> set[str]:
    """Real tables referenced by sql (excluding CTE alias names), via sqlglot."""
    try:
        cte_names: set[str] = set()
        found: set[str] = set()
        for stmt in sqlglot.parse(sql, read="postgres"):
            if stmt is None:
                continue
            for cte in stmt.find_all(exp.CTE):
                if cte.alias:
                    cte_names.add(cte.alias.lower())
            for table in stmt.find_all(exp.Table):
                found.add(table.name)
        return {name for name in found if name.lower() not in cte_names}
    except Exception:
        return set()


def _format_rows(rows: list) -> str:
    """String observation the model consumes — matches the legacy tool's
    list-of-tuples repr."""
    return str([tuple(r) for r in rows])


def sql_literal(value: str) -> str:
    """A safely single-quoted SQL string literal, for interpolating
    untrusted values (e.g. a team name from the model) into typed-tool SQL.
    sqlglot escapes embedded quotes, so the value can only ever be a string
    literal — and the whole statement is still re-parsed by the guard."""
    return exp.Literal.string(value).sql(dialect="postgres")


def _collector_for(ctx) -> SqlCollector | None:
    """The run's shared SqlCollector, created on the context's metadata on first
    use. None for the unscoped path (no context)."""
    if ctx is None:
        return None
    collector = ctx.metadata.get("_sql_collector")
    if collector is None:
        collector = SqlCollector()
        ctx.metadata["_sql_collector"] = collector
    return collector


def run_via_guard(ctx, sql: str) -> str:
    """Execute SQL through the guard for this context. Shared by the free-form
    SQL tool and the typed tools so scope injection, forbidden-column blocking,
    and telemetry all happen in one place."""
    rbac = ctx.rbac if ctx is not None else None
    return execute_guarded_sql(sql, rbac, _collector_for(ctx))


def execute_guarded_sql(sql: str, rbac_ctx, collector: SqlCollector | None = None) -> str:
    """Guard, execute (read-only ERP), and return the row observation.

    rbac_ctx is a core.rbac.context.RBACContext or None (unscoped). Guard
    rejections return "Error: ...". On success, records telemetry into
    `collector` when provided.
    """
    try:
        safe_sql = rewrite_sql(sql, rbac_ctx)
    except ValueError as exc:
        return f"Error: {exc}"

    with erp_engine().connect() as conn:
        rows = conn.execute(sqlalchemy.text(safe_sql)).fetchall()

    if collector is not None:
        collector.record(safe_sql, _tables_in_sql(safe_sql), len(rows))
    return _format_rows(rows)


class _SqlInput(BaseModel):
    sql: str = Field(description="A single read-only SQL SELECT statement for the ERP database.")


def _run(ctx, sql: str) -> str:
    return run_via_guard(ctx, sql)


QUERY_ERP_SQL = Tool(
    name="query_erp_sql",
    description=(
        "Run a read-only SQL SELECT against the HR/ERP database and return the "
        "rows. Use for any question not served by a purpose-built tool."
    ),
    input_model=_SqlInput,
    required_permissions=("sql.execute",),
    fn=_run,
)
