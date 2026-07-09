"""drop audit log

Revision ID: 0002_drop_audit_log
Revises: 50984e4fef6b
Create Date: 2026-07-08 00:00:00.000000

"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0002_drop_audit_log"
down_revision: str | Sequence[str] | None = "50984e4fef6b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_index(op.f("ix_hr_assistant_audit_slack_user_id"), table_name="hr_assistant_audit")
    op.drop_table("hr_assistant_audit")


def downgrade() -> None:
    """Downgrade schema."""
    op.create_table(
        "hr_assistant_audit",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("slack_user_id", sa.String(length=20), nullable=True),
        sa.Column("employee_id", sa.Integer(), nullable=True),
        sa.Column("role", sa.String(length=20), nullable=True),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("answer", sa.Text(), nullable=True),
        sa.Column("tables_accessed", sa.String(length=500), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("schema_rag_ms", sa.Integer(), nullable=True),
        sa.Column("agent_ms", sa.Integer(), nullable=True),
        sa.Column("total_ms", sa.Integer(), nullable=True),
        sa.Column("user_lookup_ms", sa.Integer(), nullable=True),
        sa.Column("rate_check_ms", sa.Integer(), nullable=True),
        sa.Column("history_fetch_ms", sa.Integer(), nullable=True),
        sa.Column("slack_post_ms", sa.Integer(), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column("total_tokens", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_hr_assistant_audit_slack_user_id"),
        "hr_assistant_audit",
        ["slack_user_id"],
        unique=False,
    )
