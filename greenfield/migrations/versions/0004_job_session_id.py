"""Give every durable job a stable root session identifier."""

import sqlalchemy as sa
from alembic import op

revision = "0004_job_session_id"
down_revision = "0003_config_revisions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "case_jobs", sa.Column("session_id", sa.String(length=255), nullable=True)
    )
    op.execute(
        sa.text(
            "UPDATE case_jobs SET session_id = "
            "CASE WHEN length('session-' || job_id) <= 255 "
            "THEN 'session-' || job_id "
            "ELSE 'session-' || left(job_id, 214) || '-' || md5(job_id) END"
        )
    )
    op.alter_column(
        "case_jobs",
        "session_id",
        existing_type=sa.String(length=255),
        nullable=False,
    )
    op.create_unique_constraint(
        "uq_case_jobs_session_id", "case_jobs", ["session_id"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_case_jobs_session_id", "case_jobs", type_="unique")
    op.drop_column("case_jobs", "session_id")
