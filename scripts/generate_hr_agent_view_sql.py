"""
Generate the SQL that provisions the least-privilege `hr_agent` view schema
and `hr_assistant_ro` role in the prod ERP database.

This script is a GENERATOR ONLY: it emits SQL text for the ERP-owning team to
review and run themselves. It never executes DDL — the DATABASE_URL connection
is used solely to introspect information_schema.columns, so view column lists
come from the live schema and the forbidden-column filter comes directly from
core.rbac.context.FORBIDDEN_COLUMNS (never hand-copied, so it cannot drift).

Design spec: docs/superpowers/specs/2026-07-10-prod-db-least-privilege-design.md

Usage:
    python scripts/generate_hr_agent_view_sql.py                 # SQL to stdout
    python scripts/generate_hr_agent_view_sql.py --out views.sql
    python scripts/generate_hr_agent_view_sql.py --check-only    # report only, no SQL

Reads DATABASE_URL from environment / .env file.
"""

import argparse
import os
import sys
from datetime import date
from pathlib import Path

# Allow running from project root or scripts/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

from core.config import settings
from core.rbac.context import FORBIDDEN_COLUMNS
from core.rbac.sql_guard import (
    _PERSON_FK_TABLES,
    _PERSON_FREE_TABLES,
    _PERSON_TEAM_FK_TABLES,
)

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "")
if not DATABASE_URL:
    sys.exit("DATABASE_URL not set. Add it to .env or export it.")
if not DATABASE_URL.startswith(("postgresql://", "postgres://")):
    sys.exit(
        "This script requires PostgreSQL. Set DATABASE_URL to a postgres:// or postgresql:// URL."
    )

VIEW_SCHEMA = "hrdb"
ROLE = "hr_assistant_ro"
SPEC_PATH = "docs/superpowers/specs/2026-07-10-prod-db-least-privilege-design.md"


def quote_ident(name: str) -> str:
    """Always double-quote identifiers — several ERP columns are reserved words
    ("end", "primary"), and unconditional quoting is safe for the rest."""
    return '"' + name.replace('"', '""') + '"'


def select_tables() -> tuple[list[str], list[str]]:
    """Split INCLUDED_TABLES into (selected, excluded).

    Selected = tables classified in sql_guard's three sets, plus `person`
    (the base table, scoped directly by the guard rather than listed in a set).
    Excluded = tables the guard cannot row-scope; per the design spec they must
    never be sent to the DBA for view provisioning.
    """
    classified = {"person"} | _PERSON_FK_TABLES | _PERSON_TEAM_FK_TABLES | _PERSON_FREE_TABLES
    included = set(settings.INCLUDED_TABLES)
    selected = sorted((included & classified) | {"person"})
    excluded = sorted(included - classified)
    return selected, excluded


def introspect_columns(tables: list[str]) -> dict[str, tuple[list[str], list[str]]]:
    """Return {table: (kept_columns, dropped_columns)}, ordered by ordinal_position.

    Fails loudly if a table is absent from information_schema.columns or has no
    columns left after removing FORBIDDEN_COLUMNS — either would mean the
    generated SQL is wrong, and a wrong view must never be handed to the DBA.
    """
    connect_args: dict = {"connect_timeout": 10}
    if "sslmode" not in DATABASE_URL:
        connect_args["sslmode"] = "prefer"
    engine = create_engine(DATABASE_URL, connect_args=connect_args)

    by_table: dict[str, list[str]] = {t: [] for t in tables}
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT table_name, column_name "
                "FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = ANY(:tables) "
                "ORDER BY table_name, ordinal_position"
            ),
            {"tables": tables},
        )
        for table_name, column_name in rows:
            by_table[table_name].append(column_name)

    missing = [t for t in tables if not by_table[t]]
    if missing:
        sys.exit(
            f"Tables not found in information_schema.columns: {', '.join(missing)}. "
            "Refusing to generate SQL — the live schema does not match the table list."
        )

    result: dict[str, tuple[list[str], list[str]]] = {}
    for table, cols in by_table.items():
        kept = [c for c in cols if c.lower() not in FORBIDDEN_COLUMNS]
        dropped = [c for c in cols if c.lower() in FORBIDDEN_COLUMNS]
        if not kept:
            sys.exit(
                f"Table '{table}' has zero columns left after removing forbidden "
                "columns — refusing to generate a nonsensical empty view."
            )
        result[table] = (kept, dropped)
    return result


def print_report(selected: list[str], excluded: list[str]) -> None:
    print(f"Tables selected for {VIEW_SCHEMA} views ({len(selected)}):", file=sys.stderr)
    for table in selected:
        print(f"  {table}", file=sys.stderr)
    if excluded:
        print(
            f"\nWARNING: {len(excluded)} table(s) in INCLUDED_TABLES are NOT classified in "
            "core/rbac/sql_guard.py and are EXCLUDED from the generated SQL:",
            file=sys.stderr,
        )
        for table in excluded:
            print(f"  {table}", file=sys.stderr)
        print(
            "Classify them in sql_guard.py (or drop them from INCLUDED_TABLES) "
            "before requesting views for them — see the design spec.",
            file=sys.stderr,
        )


def generate_sql(
    selected: list[str],
    excluded: list[str],
    columns: dict[str, tuple[list[str], list[str]]],
) -> str:
    out: list[str] = [
        f"-- Generated by scripts/generate_hr_agent_view_sql.py on {date.today().isoformat()}.",
        "-- Do not edit by hand — regenerate instead.",
        f"-- Design spec: {SPEC_PATH}",
        "-- For the ERP-owning team: review, then run against the prod ERP database.",
        "",
        f"CREATE SCHEMA IF NOT EXISTS {VIEW_SCHEMA};",
        "",
    ]

    for table in selected:
        kept, _ = columns[table]
        col_list = ", ".join(quote_ident(c) for c in kept)
        out.append(
            f"CREATE OR REPLACE VIEW {VIEW_SCHEMA}.{quote_ident(table)} AS\n"
            f"SELECT {col_list}\n"
            f"FROM public.{quote_ident(table)};"
        )
        out.append("")

    out += [
        f"-- {ROLE}: dedicated role for the HR assistant app only.",
        "-- NOTE (owning team): add LOGIN and authentication (password, cert, ...)",
        "-- appropriate to your environment — deliberately not baked in here.",
        "DO $$",
        "BEGIN",
        f"    CREATE ROLE {ROLE};",
        "EXCEPTION",
        "    WHEN duplicate_object THEN NULL;",
        "END",
        "$$;",
        "",
        f"GRANT USAGE ON SCHEMA {VIEW_SCHEMA} TO {ROLE};",
        f"GRANT SELECT ON ALL TABLES IN SCHEMA {VIEW_SCHEMA} TO {ROLE};",
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA {VIEW_SCHEMA} GRANT SELECT ON TABLES TO {ROLE};",
        "",
        f"-- Belt-and-suspenders: {ROLE} must have no access to the base ERP schema,",
        "-- even if a pre-existing blanket grant to PUBLIC covers public.* — these",
        "-- revokes make that explicit rather than assumed.",
        f"REVOKE ALL ON SCHEMA public FROM {ROLE};",
        f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {ROLE};",
        "",
        "-- " + "-" * 74,
        "-- Provisioning report",
        f"-- Views created in {VIEW_SCHEMA} ({len(selected)} tables):",
    ]
    for table in selected:
        kept, dropped = columns[table]
        dropped_note = ", ".join(dropped) if dropped else "none"
        out.append(f"--   {table}: {len(kept)} columns (forbidden columns removed: {dropped_note})")
    if excluded:
        out += [
            "--",
            "-- Tables in INCLUDED_TABLES but NOT provisioned — unclassified in",
            "-- core/rbac/sql_guard.py, so the app cannot row-scope them; classify them",
            "-- there (or drop them from INCLUDED_TABLES) before requesting views:",
        ]
        out += [f"--   {table}" for table in excluded]
    out += [
        "--",
        "-- Forbidden-column source: core.rbac.context.FORBIDDEN_COLUMNS",
        f"--   ({', '.join(sorted(FORBIDDEN_COLUMNS))})",
    ]
    return "\n".join(out) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate hr_agent view-schema SQL for the ERP-owning team"
    )
    parser.add_argument("--out", help="Write generated SQL to this file instead of stdout")
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Print the table selection/exclusion report to stderr and exit; generate no SQL",
    )
    args = parser.parse_args()

    selected, excluded = select_tables()
    print_report(selected, excluded)
    if args.check_only:
        return

    columns = introspect_columns(selected)
    sql = generate_sql(selected, excluded, columns)
    if args.out:
        Path(args.out).write_text(sql)
        print(f"\nWrote SQL to {args.out}", file=sys.stderr)
    else:
        print(sql)


if __name__ == "__main__":
    main()
