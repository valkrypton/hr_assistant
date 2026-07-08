"""
Shared database engines and session helper.

Lives in core/ (not api/) so both the API layer and the messaging adapters can
use one set of engine factories — adapters/ must not import from api/ (see
AGENTS.md import rules), which previously forced the Slack adapter to duplicate
these helpers.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

import sqlalchemy
from sqlalchemy.orm import Session

from core.config import settings


@lru_cache(maxsize=1)
def app_engine():
    """Writable engine for our own tables (hr_assistant_users, audit logs, etc.)."""
    return sqlalchemy.create_engine(settings.APP_DATABASE_URL)


@lru_cache(maxsize=1)
def erp_engine():
    """Read-only ERP engine — used only for the health check."""
    return sqlalchemy.create_engine(settings.DATABASE_URL)


@contextmanager
def db_session() -> Iterator[Session]:
    """Open a Session on the app engine. Usable outside a request (e.g. the
    Slack background task) as well as inside one. Short-lived by design — never
    held across the ~15s agent call, so a pool connection isn't parked idle."""
    with Session(app_engine()) as session:
        yield session
