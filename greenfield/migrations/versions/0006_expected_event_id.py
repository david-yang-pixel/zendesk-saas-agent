"""Persist the customer event cursor each sandbox must observe."""

import sqlalchemy as sa
from alembic import op

revision = "0006_expected_event_id"
down_revision = "0005_run_telemetry"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "case_jobs",
        sa.Column("expected_event_id", sa.String(length=255), nullable=True),
    )
    op.execute("UPDATE case_jobs SET expected_event_id = event_id")
    op.alter_column("case_jobs", "expected_event_id", nullable=False)


def downgrade() -> None:
    op.drop_column("case_jobs", "expected_event_id")
