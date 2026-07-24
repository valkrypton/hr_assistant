"""
Database engines and session helpers.

Framework-agnostic (pure SQLAlchemy, no FastAPI types) so both api/ and
adapters/ can use it directly — previously api/deps.py owned this and
adapters/slack.py duplicated its own private copy, since adapters/ cannot
import from api/ (see AGENTS.md's import rules; core/ has zero dependency on
api/, and api/ + adapters/ both depend inward on core/).
"""

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

import sqlalchemy
from sqlalchemy.orm import Session

from core.config import settings


@lru_cache(maxsize=1)
def app_engine():
    """Writable engine for our own tables (hr_assistant_users, hr_admin_users)."""
    return sqlalchemy.create_engine(settings.APP_DATABASE_URL, pool_pre_ping=True, pool_recycle=300)


@lru_cache(maxsize=1)
def erp_engine():
    """Read-only ERP engine — used only for the health check."""
    return sqlalchemy.create_engine(settings.DATABASE_URL, pool_pre_ping=True, pool_recycle=300)


@contextmanager
def db_session() -> Iterator[Session]:
    """Open a Session on the app engine. Usable outside a request (e.g. the
    Slack background task) as well as inside one (api.deps.get_db wraps
    this for the FastAPI-dependency form)."""
    with Session(app_engine()) as session:
        yield session
