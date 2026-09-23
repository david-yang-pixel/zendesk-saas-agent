"""Fence each operator-mention internal draft before the Zendesk write."""

import sqlalchemy as sa
from alembic import op

revision = "0010_internal_draft_deliveries"
down_revision = "0009_run_log_tool_exports"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "internal_draft_deliveries",
        sa.Column("job_id", sa.String(255), sa.ForeignKey("case_jobs.job_id"), primary_key=True),
        sa.Column("ticket_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("attempted_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("ticket_id > 0", name="ck_internal_draft_deliveries_ticket_id"),
        sa.CheckConstraint("status IN ('attempting', 'sent')", name="ck_internal_draft_deliveries_status"),
    )


def downgrade() -> None:
    op.drop_table("internal_draft_deliveries")
