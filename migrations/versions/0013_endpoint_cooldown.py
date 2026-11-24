"""Persist the model quota cooldown shared by Pi Agent workers."""

import sqlalchemy as sa
from alembic import op

revision = "0013_endpoint_cooldown"
down_revision = "0012_message_coverage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "model_endpoint_cooldowns",
        sa.Column("endpoint_key", sa.String(64), primary_key=True),
        sa.Column("window_id", sa.Uuid(), nullable=False),
        sa.Column("retry_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consecutive_windows", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column("case_jobs", sa.Column("snapshot_wait_since", sa.DateTime(timezone=True)))
    op.add_column("case_jobs", sa.Column("snapshot_alert_after", sa.DateTime(timezone=True)))


def downgrade() -> None:
    op.drop_column("case_jobs", "snapshot_alert_after")
    op.drop_column("case_jobs", "snapshot_wait_since")
    op.drop_table("model_endpoint_cooldowns")
