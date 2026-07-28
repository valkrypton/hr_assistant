"""
SQLAlchemy models for the HR Assistant app database.

All tables here live in APP_DATABASE_URL (never the ERP).
The agent never sees these tables — they are not in INCLUDED_TABLES.

Tables
------
hr_assistant_users   — registered users with roles and Slack identity
slack_seen_events    — Slack event_id dedupe store (shared across workers)
"""

from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class AdminUser(Base):
    """
    Superusers who can access the /users API route and /admin panel.
    Separate from HRUser — HR users query the bot, admin users manage it.
    Create via: python scripts/create_admin.py <username>
    """

    __tablename__ = "hr_admin_users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(64), nullable=False, unique=True, index=True)
    hashed_password = Column(String(128), nullable=False)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )

    def __repr__(self) -> str:
        return f"<AdminUser id={self.id} username={self.username} active={self.is_active}>"


class HRUser(Base):
    __tablename__ = "hr_assistant_users"
    __table_args__ = (UniqueConstraint("slack_user_id", name="uq_hr_user_slack"),)

    id = Column(Integer, primary_key=True, autoincrement=True)

    # Link to the ERP person row — not a FK so the table works even if
    # the ERP schema changes or lives on a different logical DB. This IS
    # person.id, and it is the sole input to RBAC resolution
    # (core/rbac/resolution.py).
    employee_id = Column(Integer, nullable=False, index=True)

    slack_user_id = Column(String(20), nullable=True)

    is_active = Column(Boolean, nullable=False, default=True)

    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    def __repr__(self) -> str:
        return f"<HRUser id={self.id} employee_id={self.employee_id} slack={self.slack_user_id}>"


class SlackSeenEvent(Base):
    """
    Dedupe store for Slack event_ids — replaces the previous in-process TTL
    dict (adapters/slack.py), which only worked for a single-worker deploy.
    The event_id primary key gives atomic "first sight wins" semantics under
    concurrent inserts (a duplicate insert raises IntegrityError) across
    workers/processes sharing this DB. Rows older than the TTL are
    opportunistically deleted on each check — see
    adapters.slack.already_processed.
    """

    __tablename__ = "slack_seen_events"

    event_id = Column(String(128), primary_key=True)
    seen_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        index=True,
    )

    def __repr__(self) -> str:
        return f"<SlackSeenEvent event_id={self.event_id} seen_at={self.seen_at}>"
