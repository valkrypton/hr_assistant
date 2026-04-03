"""
HR Agent — core layer.

This module has NO dependency on the API layer.  It can be imported and used
standalone (scripts, tests, notebooks) without starting a web server.
"""

import sqlalchemy
from langchain_community.utilities import SQLDatabase
from langchain_community.agent_toolkits import create_sql_agent

from core.config import settings
from core.providers.factory import get_llm

_PREFIX = """You are an expert HR data analyst with read-only access to the
company database. Answer questions accurately using SQL queries.

Rules:
- Only use SELECT statements — never INSERT, UPDATE, DELETE, or DROP.
- If the answer cannot be determined from the available data, say so clearly.
- Present numbers, dates, and names in a human-friendly format.
- When listing employees always include their full name and department."""


def _get_included_tables(database_url: str) -> list[str]:
    """Return all table names minus the ones in IGNORED_TABLES."""
    engine = sqlalchemy.create_engine(database_url)
    inspector = sqlalchemy.inspect(engine)
    all_tables = inspector.get_table_names()
    ignored = set(settings.IGNORED_TABLES)
    return [t for t in all_tables if t not in ignored]


def build_agent():
    """Construct and return a fresh SQL agent executor."""
    llm = get_llm()

    included = _get_included_tables(settings.DATABASE_URL)
    db = SQLDatabase.from_uri(
        settings.DATABASE_URL,
        include_tables=included,
        sample_rows_in_table_info=3,
    )

    return create_sql_agent(
        llm=llm,
        db=db,
        verbose=True,
        prefix=_PREFIX,
        handle_parsing_errors=True,
        max_iterations=10,
    )


def query(user_input: str) -> str:
    """Run a natural-language HR query and return the answer string."""
    agent_executor = build_agent()
    result = agent_executor.invoke({"input": user_input})
    return result.get("output", str(result))
