"""
SQLAlchemy models for the HR Assistant app database.

All tables here live in APP_DATABASE_URL (never the ERP).
The agent never sees these tables — they are not in INCLUDED_TABLES.

Tables
------
hr_assistant_users   — registered users with roles and Slack identity
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
        default=lambda: datetime.now(UTC),
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    def __repr__(self) -> str:
        return (
            f"<HRUser id={self.id} employee_id={self.employee_id} "
            f"role={self.role} slack={self.slack_user_id}>"
        )
