"""drop role/department_id/team_id from hr_assistant_users

Revision ID: 0005_drop_hruser_role_scope
Revises: 0004_widen_slack_event_id
Create Date: 2026-07-27 00:00:00.000000

RBAC is now resolved from ERP group membership at request time
(core/rbac/resolution.py); these columns are no longer read by anything.

IRREVERSIBLE IN PRACTICE: downgrade() recreates the columns but cannot
restore their values. Snapshot hr_assistant_users before upgrading.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0005_drop_hruser_role_scope"
down_revision: str | Sequence[str] | None = "0004_widen_slack_event_id"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("hr_assistant_users") as batch:
        batch.drop_column("role")
        batch.drop_column("department_id")
        batch.drop_column("team_id")


def downgrade() -> None:
    """Downgrade schema."""
    # Columns come back empty — the role/scope values are not recoverable.
    with op.batch_alter_table("hr_assistant_users") as batch:
        batch.add_column(sa.Column("role", sa.String(length=20), nullable=True))
        batch.add_column(sa.Column("department_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("team_id", sa.Integer(), nullable=True))
