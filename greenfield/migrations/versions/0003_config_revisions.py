"""Add append-only management configuration revisions."""

import sqlalchemy as sa
from alembic import op

revision = "0003_config_revisions"
down_revision = "0002_positive_generation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "config_revisions",
        sa.Column("revision", sa.String(length=255), primary_key=True),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("actor", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_table(
        "config_head",
        sa.Column(
            "head_id",
            sa.String(length=32),
            primary_key=True,
            server_default="current",
        ),
        sa.Column("revision", sa.String(length=255), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["revision"], ["config_revisions.revision"]),
        sa.CheckConstraint("head_id = 'current'", name="ck_config_head_singleton"),
    )


def downgrade() -> None:
    op.drop_table("config_head")
    op.drop_table("config_revisions")
