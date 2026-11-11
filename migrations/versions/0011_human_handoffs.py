"""Record the follow-up work for each customer human command."""

import sqlalchemy as sa
from alembic import op

revision = "0011_human_handoffs"
down_revision = "0010_internal_draft_deliveries"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "human_handoffs",
        sa.Column(
            "handoff_id",
            sa.String(255),
            sa.ForeignKey("events.event_id"),
            primary_key=True,
        ),
        sa.Column("ticket_id", sa.BigInteger(), nullable=False),
        sa.Column("acknowledge", sa.Boolean(), nullable=False),
        sa.Column("customer_message", sa.Text()),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("alerted_at", sa.DateTime(timezone=True)),
        sa.Column("tagged_at", sa.DateTime(timezone=True)),
        sa.Column("ack_state", sa.String(20)),
        sa.Column("ack_sent_at", sa.DateTime(timezone=True)),
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
        sa.CheckConstraint("ticket_id > 0", name="ck_human_handoffs_ticket_id_positive"),
        sa.CheckConstraint(
            "status IN ('pending', 'processing', 'completed', 'failed')",
            name="ck_human_handoffs_status",
        ),
        sa.CheckConstraint(
            "ack_state IS NULL OR ack_state IN "
            "('attempting', 'sent', 'skipped', 'failed', 'uncertain')",
            name="ck_human_handoffs_ack_state",
        ),
    )
    op.create_index(
        "ix_human_handoffs_claimable", "human_handoffs", ["status", "available_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_human_handoffs_claimable", table_name="human_handoffs")
    op.drop_table("human_handoffs")
