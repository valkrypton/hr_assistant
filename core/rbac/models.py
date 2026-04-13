"""
SQLAlchemy models for the HR Assistant app database.

All tables here live in APP_DATABASE_URL (never the ERP).
The agent never sees these tables — they are not in INCLUDED_TABLES.

Tables
------
hr_assistant_users   — registered users with roles and Slack identity
hr_assistant_audit   — append-only query audit log (FR-6)
"""
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class HRUser(Base):
    __tablename__ = "hr_assistant_users"
    __table_args__ = (
        UniqueConstraint("slack_user_id", name="uq_hr_user_slack"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)

    # Link to the ERP person row — not a FK so the table works even if
    # the ERP schema changes or lives on a different logical DB.
    employee_id = Column(Integer, nullable=False, index=True)

    role = Column(String(20), nullable=False)

    slack_user_id = Column(String(20), nullable=True)

    # Scope columns — only relevant for DEPT_HEAD and TEAM_LEAD.
    # CTO/CEO and HR_MANAGER leave these NULL (full access).
    department_id = Column(Integer, nullable=True)
    team_id = Column(Integer, nullable=True)

    is_active = Column(Boolean, nullable=False, default=True)

    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    def __repr__(self) -> str:
        return (
            f"<HRUser id={self.id} employee_id={self.employee_id} "
            f"role={self.role} slack={self.slack_user_id}>"
        )


class AuditLog(Base):
    """
    Append-only audit log for every query made through the HR agent (FR-6).

    Rows are never updated or deleted — enforce this at the DB level by
    revoking UPDATE/DELETE on this table from the app role.

    Schema
    ------
    hr_assistant_audit
      id               SERIAL PRIMARY KEY
      slack_user_id    VARCHAR(20)            -- NULL for unauthenticated requests
      employee_id      INTEGER                -- NULL for unauthenticated requests
      role             VARCHAR(20)            -- NULL for unauthenticated requests
      question         TEXT NOT NULL          -- raw user question
      answer           TEXT                   -- agent response (may be NULL on error)
      tables_accessed  VARCHAR(500)           -- comma-separated tables used
      error            TEXT                   -- populated if query raised an exception
      created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
    """
    __tablename__ = "hr_assistant_audit"

    id = Column(Integer, primary_key=True, autoincrement=True)

    slack_user_id = Column(String(20), nullable=True, index=True)
    employee_id = Column(Integer, nullable=True)
    role = Column(String(20), nullable=True)

    question = Column(Text, nullable=False)
    answer = Column(Text, nullable=True)
    tables_accessed = Column(String(500), nullable=True)
    error = Column(Text, nullable=True)

    # Latency breakdown in milliseconds (Phase 4)
    schema_rag_ms = Column(Integer, nullable=True)   # Chroma retrieval time
    agent_ms = Column(Integer, nullable=True)         # LLM + SQL execution time
    total_ms = Column(Integer, nullable=True)         # full round-trip

    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    def __repr__(self) -> str:
        return (
            f"<AuditLog id={self.id} slack={self.slack_user_id} "
            f"role={self.role} at={self.created_at}>"
        )
