"""Persist public reply authorization, evidence and unresolved-send recovery."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0014_public_delivery"
down_revision = "0013_endpoint_cooldown"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "public_deliveries",
        sa.Column("job_id", sa.String(255), sa.ForeignKey("case_jobs.job_id"), primary_key=True),
        sa.Column("delivery_id", sa.Uuid(), nullable=False, unique=True),
        sa.Column("ticket_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("messages", postgresql.JSONB(), nullable=False),
        sa.Column("reply_body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("confirmed_at", sa.DateTime(timezone=True)),
        sa.Column("receipt", postgresql.JSONB()),
        sa.Column("last_error", sa.Text()),
        sa.Column("next_check_at", sa.DateTime(timezone=True)),
        sa.Column("alert_sent_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "status IN ('reserved', 'abandoned', 'pending', 'uncertain', 'sent', 'staff_handled')",
            name="ck_public_deliveries_status",
        ),
        sa.CheckConstraint(
            "(status = 'sent') = (confirmed_at IS NOT NULL AND receipt IS NOT NULL)",
            name="ck_public_deliveries_receipt",
        ),
        sa.CheckConstraint(
            "(status IN ('pending', 'uncertain', 'sent', 'staff_handled')) = (started_at IS NOT NULL)",
            name="ck_public_deliveries_started",
        ),
        sa.CheckConstraint(
            "(status IN ('pending', 'uncertain')) = (next_check_at IS NOT NULL)",
            name="ck_public_deliveries_next_check",
        ),
    )
    # Conversation snapshots can contain aliases or messages absent from ingress.
    # Fence the whole conversation until an ambiguous write is resolved.
    unresolved = sa.text("status IN ('pending', 'uncertain')")
    op.create_index(
        "ix_public_deliveries_sent_ticket", "public_deliveries", ["ticket_id"],
        postgresql_where=sa.text("status = 'sent'"),
    )
    op.create_index(
        "uq_public_deliveries_unresolved_ticket", "public_deliveries", ["ticket_id"],
        unique=True, postgresql_where=unresolved,
    )
    op.create_index(
        "ix_public_deliveries_reconcile", "public_deliveries", ["next_check_at", "job_id"],
        postgresql_where=unresolved,
    )


def downgrade() -> None:
    # Rolling back the table would allow any unresolved write to be sent again.
    op.execute("LOCK TABLE public_deliveries IN ACCESS EXCLUSIVE MODE")
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM public_deliveries WHERE status IN ('pending', 'uncertain')) THEN
                RAISE EXCEPTION 'Cannot downgrade while public deliveries are unresolved';
            END IF;
        END $$
    """)
    op.drop_table("public_deliveries")
