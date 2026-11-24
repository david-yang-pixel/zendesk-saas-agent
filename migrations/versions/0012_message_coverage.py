"""Track exact customer message coverage and preserve queued operator mentions."""

import sqlalchemy as sa
from alembic import op

revision = "0012_message_coverage"
down_revision = "0011_human_handoffs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ticket_states", sa.Column(
        "latest_customer_seq", sa.BigInteger(), nullable=False, server_default="0"
    ))
    op.add_column("ticket_states", sa.Column(
        "answered_customer_seq", sa.BigInteger(), nullable=False, server_default="0"
    ))
    op.add_column("ticket_states", sa.Column("latest_customer_at", sa.DateTime(timezone=True)))
    op.add_column("ticket_states", sa.Column("answered_by_job_id", sa.Text()))
    op.add_column("events", sa.Column("answered_by_staff", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("events", sa.Column("customer_seq", sa.BigInteger()))
    op.add_column("events", sa.Column("answered_by_job_id", sa.Text()))
    op.create_index("ix_events_ticket_message_id", "events", ["ticket_id", "message_id"])
    op.create_index(
        "uq_events_ticket_customer_seq", "events", ["ticket_id", "customer_seq"], unique=True
    )
    op.add_column("case_jobs", sa.Column(
        "is_operator_mention", sa.Boolean(), nullable=False, server_default=sa.false()
    ))
    op.execute("""
        UPDATE case_jobs AS job
        SET is_operator_mention = TRUE
        FROM events AS event
        WHERE job.event_id = event.event_id
          AND (job.event_id LIKE 'mention:%' OR job.expected_event_id LIKE 'mention:%'
               OR event.payload->>'trigger' = 'operator_mention')
    """)
    op.drop_index("uq_case_jobs_one_queued_ticket", table_name="case_jobs")
    op.create_index(
        "uq_case_jobs_one_queued_ticket", "case_jobs", ["ticket_id"], unique=True,
        postgresql_where=sa.text("status = 'queued' AND NOT is_operator_mention"),
    )


def downgrade() -> None:
    op.drop_index("uq_case_jobs_one_queued_ticket", table_name="case_jobs")
    # Recreating the earlier unique index fails transactionally if pending
    # mentions and customer jobs still coexist. Drain those jobs before retrying.
    op.create_index(
        "uq_case_jobs_one_queued_ticket", "case_jobs", ["ticket_id"], unique=True,
        postgresql_where=sa.text("status = 'queued'"),
    )
    op.drop_column("case_jobs", "is_operator_mention")
    op.drop_index("uq_events_ticket_customer_seq", table_name="events")
    op.drop_index("ix_events_ticket_message_id", table_name="events")
    op.drop_column("events", "answered_by_job_id")
    op.drop_column("events", "answered_by_staff")
    op.drop_column("events", "customer_seq")
    op.drop_column("ticket_states", "answered_by_job_id")
    op.drop_column("ticket_states", "latest_customer_at")
    op.drop_column("ticket_states", "answered_customer_seq")
    op.drop_column("ticket_states", "latest_customer_seq")
