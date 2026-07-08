"""audit observability columns + append-only enforcement

Adds tools_used / sql_statements / model_name / rows_returned to
hr_assistant_audit (PR9), and — on PostgreSQL — a BEFORE UPDATE/DELETE trigger
that makes the audit table append-only at the database level (security audit
finding #6). The trigger needs no role name, so it holds regardless of which
role connects. On SQLite (local dev / tests) only the columns are added.

Revision ID: 0002_audit_observability
Revises: 50984e4fef6b
Create Date: 2026-07-09

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002_audit_observability"
down_revision: str | Sequence[str] | None = "50984e4fef6b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "hr_assistant_audit"

_TRIGGER_UP = """
CREATE OR REPLACE FUNCTION hr_assistant_audit_no_mutate() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'hr_assistant_audit is append-only: % is not allowed', TG_OP;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER hr_assistant_audit_append_only
  BEFORE UPDATE OR DELETE ON hr_assistant_audit
  FOR EACH ROW EXECUTE FUNCTION hr_assistant_audit_no_mutate();
"""

_TRIGGER_DOWN = """
DROP TRIGGER IF EXISTS hr_assistant_audit_append_only ON hr_assistant_audit;
DROP FUNCTION IF EXISTS hr_assistant_audit_no_mutate();
"""


def upgrade() -> None:
    op.add_column(_TABLE, sa.Column("tools_used", sa.String(length=500), nullable=True))
    op.add_column(_TABLE, sa.Column("sql_statements", sa.Text(), nullable=True))
    op.add_column(_TABLE, sa.Column("model_name", sa.String(length=100), nullable=True))
    op.add_column(_TABLE, sa.Column("rows_returned", sa.Integer(), nullable=True))

    if op.get_bind().dialect.name == "postgresql":
        op.execute(_TRIGGER_UP)


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(_TRIGGER_DOWN)

    op.drop_column(_TABLE, "rows_returned")
    op.drop_column(_TABLE, "model_name")
    op.drop_column(_TABLE, "sql_statements")
    op.drop_column(_TABLE, "tools_used")
