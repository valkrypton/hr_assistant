#!/usr/bin/env python3
"""
Ad-hoc ERP query tool — run raw SQL against DATABASE_URL for local testing.

Usage:
    python scripts/query_erp.py "SELECT full_name FROM person LIMIT 5"
    python scripts/query_erp.py               # interactive mode, one query per line, empty line to quit
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import sqlalchemy
from sqlalchemy.engine import make_url

from core.config import settings

_engine = sqlalchemy.create_engine(settings.DATABASE_URL)


def run(sql: str) -> None:
    with _engine.connect() as conn:
        result = conn.execute(sqlalchemy.text(sql))
        if not result.returns_rows:
            print(f"OK — {result.rowcount} row(s) affected")
            return
        rows = result.fetchall()
        cols = list(result.keys())
        print(" | ".join(cols))
        for row in rows:
            print(" | ".join(str(v) for v in row))
        print(f"\n({len(rows)} row(s))")


def main() -> None:
    if len(sys.argv) > 1:
        run(" ".join(sys.argv[1:]))
        return

    _safe_url = make_url(settings.DATABASE_URL).render_as_string(hide_password=True)
    print(f"Connected to {_safe_url} — enter SQL, empty line to quit.")
    while True:
        try:
            sql = input("sql> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not sql:
            break
        try:
            run(sql)
        except Exception as exc:
            print(f"Error: {exc}")


if __name__ == "__main__":
    main()
