"""widen slack_seen_events.event_id and add seen_at index

Revision ID: 0004_widen_slack_event_id
Revises: 0003_slack_seen_events
Create Date: 2026-07-24 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004_widen_slack_event_id"
down_revision: str | Sequence[str] | None = "0003_slack_seen_events"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.alter_column(
        "slack_seen_events",
        "event_id",
        existing_type=sa.String(length=64),
        type_=sa.String(length=128),
        existing_nullable=False,
    )
    op.create_index(
        op.f("ix_slack_seen_events_seen_at"),
        "slack_seen_events",
        ["seen_at"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_slack_seen_events_seen_at"), table_name="slack_seen_events")
    op.alter_column(
        "slack_seen_events",
        "event_id",
        existing_type=sa.String(length=128),
        type_=sa.String(length=64),
        existing_nullable=False,
    )
