"""Persist the latest explicit human-handoff resume per ticket."""

import sqlalchemy as sa
from alembic import op

revision = "0015_handoff_resume"
down_revision = "0014_public_delivery"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ticket_states", sa.Column("human_resumed_at", sa.DateTime(timezone=True)))
    op.add_column("ticket_states", sa.Column("human_resume_cursor", sa.String(255)))


def downgrade() -> None:
    op.drop_column("ticket_states", "human_resume_cursor")
    op.drop_column("ticket_states", "human_resumed_at")
