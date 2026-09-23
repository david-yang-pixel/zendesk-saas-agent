"""Checkpoint remote round IDs and content-free tool-call exports."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0009_run_log_tool_exports"
down_revision = "0008_run_log_exports"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "run_log_exports",
        sa.Column("round_remote_ids", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    op.add_column(
        "run_log_exports",
        sa.Column("tool_calls_reported", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
    )


def downgrade() -> None:
    op.drop_column("run_log_exports", "tool_calls_reported")
    op.drop_column("run_log_exports", "round_remote_ids")
