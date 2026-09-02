"""Allow one queued successor while an older ticket job is running."""

import sqlalchemy as sa
from alembic import op

revision = "0007_queued_successor"
down_revision = "0006_expected_event_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index("uq_case_jobs_one_active_ticket", table_name="case_jobs")
    op.create_index(
        "uq_case_jobs_one_queued_ticket",
        "case_jobs",
        ["ticket_id"],
        unique=True,
        postgresql_where=sa.text("status = 'queued'"),
    )


def downgrade() -> None:
    op.drop_index("uq_case_jobs_one_queued_ticket", table_name="case_jobs")
    op.create_index(
        "uq_case_jobs_one_active_ticket",
        "case_jobs",
        ["ticket_id"],
        unique=True,
        postgresql_where=sa.text(
            "status IN ('queued', 'claimed', 'sandbox_creating', "
            "'running', 'finalizing')"
        ),
    )
