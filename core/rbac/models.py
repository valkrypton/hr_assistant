"""
SQLAlchemy model for the hr_assistant_users table.

This table lives in the same PostgreSQL database as the ERP but is owned
by this application — the agent never sees it (it is not in INCLUDED_TABLES).

Schema
------
hr_assistant_users
  id               SERIAL PRIMARY KEY
  employee_id      INTEGER NOT NULL          -- references person.id in the ERP
  role             VARCHAR(20) NOT NULL      -- Role enum value
  slack_user_id    VARCHAR(20) UNIQUE        -- Slack member ID, e.g. U01ABCDEF
  department_id    INTEGER                   -- scope for DEPT_HEAD
  team_id          INTEGER                   -- scope for TEAM_LEAD
  is_active        BOOLEAN NOT NULL DEFAULT TRUE
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
"""
from datetime import datetime, timezone

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
