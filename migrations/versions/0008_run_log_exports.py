"""Track resumable SaaS run and round exports to the manager log view."""

import sqlalchemy as sa
from alembic import op

revision = "0008_run_log_exports"
down_revision = "0007_queued_successor"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "run_log_exports",
        sa.Column("job_id", sa.String(255), sa.ForeignKey("case_jobs.job_id"), primary_key=True),
        sa.Column("remote_run_id", sa.Integer(), nullable=True),
        sa.Column("rounds_reported", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("pending_operation", sa.String(50), nullable=True),
        sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("rounds_reported >= 0", name="ck_run_log_exports_rounds_reported"),
    )


def downgrade() -> None:
    op.drop_table("run_log_exports")
