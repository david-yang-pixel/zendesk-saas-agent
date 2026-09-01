"""Create the durable Casework queue and delivery fence."""

import sqlalchemy as sa
from alembic import op

revision = "0001_casework_persistence"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "events",
        sa.Column("event_id", sa.String(length=255), primary_key=True),
        sa.Column("ticket_id", sa.BigInteger(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(length=100), nullable=False),
        sa.Column("customer_origin", sa.String(length=100), nullable=False),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("message_id", sa.String(length=255)),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("ticket_id > 0", name="ck_events_ticket_id_positive"),
    )
    op.create_index("ix_events_ticket_id", "events", ["ticket_id"])

    op.create_table(
        "ticket_states",
        sa.Column("ticket_id", sa.BigInteger(), primary_key=True, autoincrement=False),
        sa.Column("generation", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("last_event_id", sa.String(length=255)),
        sa.Column("human_cancelled_at", sa.DateTime(timezone=True)),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["last_event_id"], ["events.event_id"]),
        sa.CheckConstraint("ticket_id > 0", name="ck_ticket_states_ticket_id_positive"),
        sa.CheckConstraint(
            "generation >= 0", name="ck_ticket_states_generation_nonnegative"
        ),
    )

    op.create_table(
        "case_jobs",
        sa.Column("job_id", sa.String(length=255), primary_key=True),
        sa.Column("event_id", sa.String(length=255), nullable=False),
        sa.Column("ticket_id", sa.BigInteger(), nullable=False),
        sa.Column("generation", sa.BigInteger(), nullable=False),
        sa.Column(
            "status", sa.String(length=32), nullable=False, server_default="queued"
        ),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("lease_owner", sa.String(length=255)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True)),
        sa.Column("config_revision", sa.String(length=255), nullable=False),
        sa.Column("root_model_config_id", sa.String(length=255), nullable=False),
        sa.Column("child_model_config_ids", sa.JSON(), nullable=False),
        sa.Column("model_digest", sa.String(length=255), nullable=False),
        sa.Column("skill_digest", sa.String(length=255), nullable=False),
        sa.Column("tool_digest", sa.String(length=255), nullable=False),
        sa.Column("dead_letter_reason", sa.Text()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["event_id"], ["events.event_id"]),
        sa.UniqueConstraint("event_id", name="uq_case_jobs_event_id"),
        sa.CheckConstraint("ticket_id > 0", name="ck_case_jobs_ticket_id_positive"),
        sa.CheckConstraint(
            "generation >= 0", name="ck_case_jobs_generation_nonnegative"
        ),
        sa.CheckConstraint("attempts >= 0", name="ck_case_jobs_attempts_nonnegative"),
        sa.CheckConstraint(
            "status IN ('queued', 'claimed', 'sandbox_creating', 'running', "
            "'finalizing', 'completed', 'awaiting_customer', 'human_takeover', "
            "'failed', 'cancelled')",
            name="ck_case_jobs_status",
        ),
    )
    op.create_index(
        "uq_case_jobs_one_active_ticket",
        "case_jobs",
        ["ticket_id"],
        unique=True,
        postgresql_where=sa.text(
            "status IN ('queued', 'claimed', 'sandbox_creating', 'running', "
            "'finalizing')"
        ),
    )
    op.create_index(
        "ix_case_jobs_claimable", "case_jobs", ["status", "available_at", "created_at"]
    )
    op.create_index(
        "ix_case_jobs_ticket_generation", "case_jobs", ["ticket_id", "generation"]
    )

    op.create_table(
        "outbox_messages",
        sa.Column("outbox_id", sa.String(length=255), primary_key=True),
        sa.Column("job_id", sa.String(length=255)),
        sa.Column("ticket_id", sa.BigInteger(), nullable=False),
        sa.Column("generation", sa.BigInteger(), nullable=False),
        sa.Column("snapshot_cursor", sa.String(length=255), nullable=False),
        sa.Column("locale", sa.String(length=32), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=512), nullable=False),
        sa.Column(
            "status", sa.String(length=32), nullable=False, server_default="pending"
        ),
        sa.Column("provider_message_id", sa.String(length=255)),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["job_id"], ["case_jobs.job_id"]),
        sa.UniqueConstraint(
            "idempotency_key", name="uq_outbox_messages_idempotency_key"
        ),
        sa.UniqueConstraint(
            "ticket_id",
            "generation",
            "snapshot_cursor",
            name="uq_outbox_ticket_generation_cursor",
        ),
        sa.CheckConstraint("ticket_id > 0", name="ck_outbox_ticket_id_positive"),
        sa.CheckConstraint("generation >= 0", name="ck_outbox_generation_nonnegative"),
        sa.CheckConstraint("attempt_count >= 0", name="ck_outbox_attempts_nonnegative"),
        sa.CheckConstraint(
            "status IN ('pending', 'attempted', 'sent', 'confirmed', "
            "'ambiguous', 'cancelled')",
            name="ck_outbox_status",
        ),
    )
    op.create_index("ix_outbox_claimable", "outbox_messages", ["status", "created_at"])
    op.create_index(
        "ix_outbox_ticket_generation", "outbox_messages", ["ticket_id", "generation"]
    )


def downgrade() -> None:
    op.drop_index("ix_outbox_ticket_generation", table_name="outbox_messages")
    op.drop_index("ix_outbox_claimable", table_name="outbox_messages")
    op.drop_table("outbox_messages")
    op.drop_index("ix_case_jobs_ticket_generation", table_name="case_jobs")
    op.drop_index("ix_case_jobs_claimable", table_name="case_jobs")
    op.drop_index("uq_case_jobs_one_active_ticket", table_name="case_jobs")
    op.drop_table("case_jobs")
    op.drop_table("ticket_states")
    op.drop_index("ix_events_ticket_id", table_name="events")
    op.drop_table("events")
