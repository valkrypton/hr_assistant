"""add slack_seen_events dedupe table

Revision ID: 0003_slack_seen_events
Revises: 0002_drop_audit_log
Create Date: 2026-07-21 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003_slack_seen_events"
down_revision: str | Sequence[str] | None = "0002_drop_audit_log"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "slack_seen_events",
        sa.Column("event_id", sa.String(length=64), nullable=False),
        sa.Column("seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("event_id"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("slack_seen_events")
