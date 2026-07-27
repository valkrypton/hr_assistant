"""
Table-name extraction from the agent's intermediate steps — used only for
the tables_accessed field on QueryResult (observability), not for RBAC
enforcement (that's core/rbac/sql_guard.py, which runs at the db.run() call
site regardless of what this module reports).

Split out of core/agent.py for the same reason as core/agent/prompts.py:
an independently readable, self-contained responsibility that nothing
patches directly.
"""

import re

import sqlglot
import sqlglot.expressions as exp


def _regex_extract_tables(sql: str) -> set[str]:
    """Fallback extractor — identifiers after FROM/JOIN keywords via regex."""
    tables: set[str] = set()
    for match in re.finditer(r'\b(?:FROM|JOIN)\s+([`"\[]?[\w]+[`"\]]?)', sql, re.IGNORECASE):
        tables.add(match.group(1).strip('`"[]'))
    return tables


def extract_tables(intermediate_steps) -> str:
    """
    Parse table names from sql_db_query tool calls in the agent's intermediate
    steps and return them as a sorted, comma-separated string.

    intermediate_steps is a list of (AgentAction, observation) tuples.
    AgentAction.tool == "sql_db_query" and AgentAction.tool_input holds the SQL.

    Uses sqlglot (already a dependency — see core/rbac/sql_guard.py) to walk
    the real parse tree so subqueries and CTEs are captured correctly, and to
    exclude CTE alias names (e.g. the "x" in "WITH x AS (...)") which are not
    real tables. Falls back to a regex over FROM/JOIN on parse failure so
    table extraction never breaks on unusual SQL.
    """
    tables: set[str] = set()
    for action, _ in intermediate_steps or []:
        tool = getattr(action, "tool", None)
        sql = getattr(action, "tool_input", None)
        if tool != "sql_db_query" or not sql:
            continue
        if isinstance(sql, dict):
            sql = sql.get("query", "")

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
            tables.update(name for name in found if name.lower() not in cte_names)
        except Exception:
            tables.update(_regex_extract_tables(sql))

    return ", ".join(sorted(tables)) if tables else ""
