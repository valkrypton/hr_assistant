"""
HR Agent - core layer.

This package has NO dependency on the API layer. It can be imported and used
standalone (scripts, tests, notebooks) without starting a web server.

Split for SRP:
    core.agent.enrichment — schema load + message assembly
    core.agent.factory    — agent construction, caches, scoped-run guard
    core.agent.runner     — query() orchestration + QueryResult

The collaborator names re-exported below (get_llm, create_sql_agent,
get_openai_callback, _extract_tables) are part of the module's test contract:
tests monkeypatch them as `core.agent.<name>`, and the submodules resolve them
from this package at call time so those patches take effect.
"""

# --- Patchable collaborators (bound before submodules import this package) ---
from langchain_community.agent_toolkits import create_sql_agent
from langchain_community.callbacks import get_openai_callback

# --- Submodules (reference the names above via `from core import agent as _pkg`) ---
from core.agent.factory import (
    _build_agent,
    _erp_db,
    _get_included_tables,
    get_agent,
    reset_shared_agent,
)
from core.agent.runner import AgentQueryResult, query
from core.agent.sql_extraction import extract_tables as _extract_tables
from core.providers.factory import get_llm

__all__ = [
    "query",
    "AgentQueryResult",
    "get_agent",
    "get_llm",
    "create_sql_agent",
    "get_openai_callback",
]
