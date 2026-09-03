"""Persist bounded SandboxResult telemetry for Admin Runs."""

import sqlalchemy as sa
from alembic import op

revision = "0005_run_telemetry"
down_revision = "0004_job_session_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "run_records",
        sa.Column("session_id", sa.String(length=255), nullable=False),
        sa.Column("job_id", sa.String(length=255), nullable=False),
        sa.Column("ticket_id", sa.BigInteger(), nullable=False),
        sa.Column("generation", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("telemetry_version", sa.String(length=64), nullable=True),
        sa.Column("active_question", sa.JSON(), nullable=True),
        sa.Column("todo_total", sa.Integer(), nullable=True),
        sa.Column("todo_completed", sa.Integer(), nullable=True),
        sa.Column("todo_statuses", sa.JSON(), nullable=True),
        sa.Column("tool_calls", sa.Integer(), nullable=True),
        sa.Column("tool_call_records", sa.JSON(), nullable=True),
        sa.Column("subagents", sa.Integer(), nullable=True),
        sa.Column("subagent_tasks", sa.JSON(), nullable=True),
        sa.Column("evidence_count", sa.Integer(), nullable=True),
        sa.Column("evidence_ids", sa.JSON(), nullable=True),
        sa.Column("context_checkpoint", sa.JSON(), nullable=True),
        sa.Column("sandbox_lifecycle", sa.JSON(), nullable=True),
        sa.Column("outcome", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "ticket_id > 0", name="ck_run_records_ticket_id_positive"
        ),
        sa.CheckConstraint(
            "generation >= 1", name="ck_run_records_generation_positive"
        ),
        sa.CheckConstraint(
            "status IN ('completed', 'awaiting_customer', 'human_takeover', "
            "'failed', 'cancelled')",
            name="ck_run_records_status",
        ),
        sa.CheckConstraint(
            "todo_total IS NULL OR todo_total >= 0",
            name="ck_run_records_todo_total_nonnegative",
        ),
        sa.CheckConstraint(
            "todo_completed IS NULL OR todo_completed >= 0",
            name="ck_run_records_todo_completed_nonnegative",
        ),
        sa.CheckConstraint(
            "tool_calls IS NULL OR tool_calls >= 0",
            name="ck_run_records_tool_calls_nonnegative",
        ),
        sa.CheckConstraint(
            "subagents IS NULL OR subagents >= 0",
            name="ck_run_records_subagents_nonnegative",
        ),
        sa.CheckConstraint(
            "evidence_count IS NULL OR evidence_count >= 0",
            name="ck_run_records_evidence_count_nonnegative",
        ),
        sa.ForeignKeyConstraint(["job_id"], ["case_jobs.job_id"]),
        sa.PrimaryKeyConstraint("session_id"),
        sa.UniqueConstraint("job_id"),
    )
    op.create_index(
        "ix_run_records_ticket_generation",
        "run_records",
        ["ticket_id", "generation"],
    )
    op.create_index("ix_run_records_finished_at", "run_records", ["finished_at"])

    op.create_table(
        "run_events",
        sa.Column("event_id", sa.String(length=255), nullable=False),
        sa.Column("session_id", sa.String(length=255), nullable=False),
        sa.Column("job_id", sa.String(length=255), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["job_id"], ["case_jobs.job_id"]),
        sa.ForeignKeyConstraint(["session_id"], ["run_records.session_id"]),
        sa.PrimaryKeyConstraint("event_id"),
    )
    op.create_index(
        "ix_run_events_session_occurred",
        "run_events",
        ["session_id", "occurred_at"],
    )
    op.create_index(
        "ix_run_events_job_occurred", "run_events", ["job_id", "occurred_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_run_events_job_occurred", table_name="run_events")
    op.drop_index("ix_run_events_session_occurred", table_name="run_events")
    op.drop_table("run_events")
    op.drop_index("ix_run_records_finished_at", table_name="run_records")
    op.drop_index("ix_run_records_ticket_generation", table_name="run_records")
    op.drop_table("run_records")
