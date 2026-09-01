"""Align durable ticket generations with the sandbox contract.

Generation zero existed only in the initial persistence slice.  Sandbox run
contracts require a positive generation, so existing rows are shifted together
before the stronger constraints are installed.
"""

import sqlalchemy as sa
from alembic import op

revision = "0002_positive_generation"
down_revision = "0001_casework_persistence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_outbox_generation_nonnegative", "outbox_messages", type_="check"
    )
    op.drop_constraint(
        "ck_case_jobs_generation_nonnegative", "case_jobs", type_="check"
    )
    op.drop_constraint(
        "ck_ticket_states_generation_nonnegative", "ticket_states", type_="check"
    )

    op.execute(sa.text("UPDATE ticket_states SET generation = generation + 1"))
    op.execute(sa.text("UPDATE case_jobs SET generation = generation + 1"))
    op.execute(sa.text("UPDATE outbox_messages SET generation = generation + 1"))

    op.alter_column(
        "ticket_states",
        "generation",
        existing_type=sa.BigInteger(),
        server_default="1",
        existing_nullable=False,
    )
    op.create_check_constraint(
        "ck_ticket_states_generation_positive", "ticket_states", "generation >= 1"
    )
    op.create_check_constraint(
        "ck_case_jobs_generation_positive", "case_jobs", "generation >= 1"
    )
    op.create_check_constraint(
        "ck_outbox_generation_positive", "outbox_messages", "generation >= 1"
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_outbox_generation_positive", "outbox_messages", type_="check"
    )
    op.drop_constraint(
        "ck_case_jobs_generation_positive", "case_jobs", type_="check"
    )
    op.drop_constraint(
        "ck_ticket_states_generation_positive", "ticket_states", type_="check"
    )

    op.execute(sa.text("UPDATE outbox_messages SET generation = generation - 1"))
    op.execute(sa.text("UPDATE case_jobs SET generation = generation - 1"))
    op.execute(sa.text("UPDATE ticket_states SET generation = generation - 1"))

    op.alter_column(
        "ticket_states",
        "generation",
        existing_type=sa.BigInteger(),
        server_default="0",
        existing_nullable=False,
    )
    op.create_check_constraint(
        "ck_ticket_states_generation_nonnegative",
        "ticket_states",
        "generation >= 0",
    )
    op.create_check_constraint(
        "ck_case_jobs_generation_nonnegative", "case_jobs", "generation >= 0"
    )
    op.create_check_constraint(
        "ck_outbox_generation_nonnegative", "outbox_messages", "generation >= 0"
    )
