"""Agent construction — the cached ERP SQLDatabase, the per-request scoped `db.run`
wrapper (rewrite_sql guard), and the shared-agent cache. Split out of the old
core/agent.py (SRP: construction vs execution)."""

import copy
from functools import lru_cache

from langchain_community.utilities import SQLDatabase

from core import agent as _pkg  # resolve get_llm/create_sql_agent/etc. at call time
from core.agent.prompts import build_prefix
from core.config import DEFAULT_ENGINE_ARGS, settings
from core.rbac.sql_guard import (
    assert_person_free_tables_have_no_person_fk,
    assert_tables_classified,
)


def _get_included_tables() -> list[str]:
    tables = settings.included_tables
    if not tables:
        raise ValueError(
            "INCLUDED_TABLES must be set in .env. "
            "List only the tables the agent needs (e.g. person,department,leave_record)."
        )
    assert_tables_classified(tables)
    return tables


# ---------------------------------------------------------------------------
# ERP SQLDatabase — built once per (url, tables), not once per request.
#
# from_uri() does create_engine() + a full MetaData.reflect() over every
# included table (lazy_table_reflection defaults to false). Building it per
# restricted-role request meant every dept_head/team_lead query paid that
# cost and leaked an undisposed engine. Cached here and shallow-copied per
# build so each caller gets its own `.run` (see _build_agent) while sharing
# the underlying engine/pool and reflected metadata. Keyed on (url, tables)
# rather than no-args so tests pointing at a throwaway sqlite file per test
# don't collide with each other or with the real ERP connection.
# ---------------------------------------------------------------------------


@lru_cache(maxsize=8)
def _erp_db(database_url: str, included_tables: tuple[str, ...]) -> SQLDatabase:
    return SQLDatabase.from_uri(
        database_url,
        include_tables=list(included_tables),
        sample_rows_in_table_info=0,
        engine_args={
            **DEFAULT_ENGINE_ARGS,
            "pool_size": settings.ERP_POOL_SIZE,
            "max_overflow": settings.ERP_MAX_OVERFLOW,
        },
    )


def _build_agent(rbac_ctx=None):
    """
    Build the SQL agent with the given RBAC context baked into the system prefix.

    When rbac_ctx is None the agent is built without scope restrictions (used
    for the shared unauthenticated agent and for superuser access).  When a
    context is provided, the role and scope are embedded in the prefix so the
    LLM treats them as immutable system rules rather than advisory hints.

    db.run is always wrapped to pass every SQL statement through
    sql_guard.rewrite_sql before execution.  This blocks non-SELECT
    statements for all roles (including CTO/CEO and unauthenticated) and
    additionally injects scope predicates for restricted roles, making
    both protections immune to prompt injection.
    """
    llm = _pkg.get_llm()
    included = tuple(_get_included_tables())
    db = copy.copy(_erp_db(settings.DATABASE_URL, included))
    # Defense-in-depth against a person-bearing table wrongly classified as
    # person-free — assert_tables_classified (above) can't catch this since
    # a mis-listed table is still, by definition, present in one of the
    # three sets.
    assert_person_free_tables_have_no_person_fk(db._metadata, list(included))

    # Imported per-call (not hoisted to module level) so tests can patch
    # core.rbac.sql_guard.rewrite_sql before this runs — see
    # tests/test_agent_scoped_run.py._build_scoped_db for why a module-level
    # alias would not be patchable the same way.
    from core.rbac.sql_guard import rewrite_sql as _rewrite

    _original_run = db.run

    def _scoped_run(command, fetch="all", **kwargs):
        try:
            command = _rewrite(command, rbac_ctx)
        except ValueError as exc:
            # Surface guard rejections (forbidden column, wildcard, non-SELECT,
            # unclassified table) to the agent as a tool observation so it can
            # rewrite the SQL.  LangChain's run_no_throw only catches
            # SQLAlchemyError, so a raised ValueError would abort the whole run.
            return f"Error: {exc}"
        return _original_run(command, fetch=fetch, **kwargs)

    db.run = _scoped_run

    prefix = build_prefix(rbac_ctx)

    return _pkg.create_sql_agent(
        llm=llm,
        db=db,
        verbose=settings.DEBUG,
        prefix=prefix,
        max_iterations=10,
        agent_type="tool-calling",
        agent_executor_kwargs={
            "handle_parsing_errors": True,
            "return_intermediate_steps": True,
        },
    )


# Shared agent for unauthenticated / superuser requests (built lazily).
_agent = None


def get_agent(rbac_ctx=None):
    """
    Return an agent appropriate for the given RBAC context.

    - No context / unrestricted → reuse the shared cached agent.
    - Restricted role (dept_head / team_lead) → build a fresh agent with the
      scope baked into the system prefix. These are not cached because each
      user has a different scope.
    """
    global _agent
    if rbac_ctx is None or rbac_ctx.is_unrestricted:
        if _agent is None:
            _agent = _pkg._build_agent(rbac_ctx)
        return _agent
    # Restricted: build per-request so the prefix carries the exact scope.
    return _pkg._build_agent(rbac_ctx)


def reset_shared_agent() -> None:
    """Drop the shared cached agent so the next build is fresh (used by the
    runner's retry after a shared-agent failure)."""
    global _agent
    _agent = None
