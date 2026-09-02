"""SQLAlchemy persistence schema for the Greenfield Casework queue.

The schema is deliberately boring: PostgreSQL is the queue and the source of
truth. State transitions are performed by repository methods, while the
database enforces event de-duplication and at most one queued successor per
ticket. A running job may coexist with that successor so customer messages are
not dropped while an older snapshot is being processed.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

ACTIVE_JOB_STATUSES = frozenset(
    {"queued", "claimed", "sandbox_creating", "running", "finalizing"}
)


class Base(DeclarativeBase):
    """Declarative metadata shared by the migration and repositories."""


class EventRecord(Base):
    __tablename__ = "events"

    event_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    ticket_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    source: Mapped[str] = mapped_column(String(100), nullable=False)
    customer_origin: Mapped[str] = mapped_column(String(100), nullable=False)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    message_id: Mapped[str | None] = mapped_column(String(255))
    payload: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint("ticket_id > 0", name="ck_events_ticket_id_positive"),
    )


class TicketState(Base):
    """One row per ticket used as the generation and cancellation fence."""

    __tablename__ = "ticket_states"

    ticket_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=False
    )
    generation: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=1, server_default="1"
    )
    last_event_id: Mapped[str | None] = mapped_column(
        String(255), ForeignKey("events.event_id")
    )
    human_cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint("ticket_id > 0", name="ck_ticket_states_ticket_id_positive"),
        CheckConstraint("generation >= 1", name="ck_ticket_states_generation_positive"),
    )


class ConfigRevisionRow(Base):
    """One immutable management configuration revision."""

    __tablename__ = "config_revisions"

    revision: Mapped[str] = mapped_column(String(255), primary_key=True)
    config: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    actor: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ConfigHeadRow(Base):
    """Singleton pointer serialized by configuration compare-and-swap writes."""

    __tablename__ = "config_head"

    head_id: Mapped[str] = mapped_column(
        String(32), primary_key=True, default="current", server_default="current"
    )
    revision: Mapped[str] = mapped_column(
        String(255), ForeignKey("config_revisions.revision"), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint("head_id = 'current'", name="ck_config_head_singleton"),
    )


class CaseJobRow(Base):
    """Durable worker queue row and immutable configuration snapshot."""

    __tablename__ = "case_jobs"

    job_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    event_id: Mapped[str] = mapped_column(
        String(255), ForeignKey("events.event_id"), nullable=False, unique=True
    )
    expected_event_id: Mapped[str] = mapped_column(String(255), nullable=False)
    ticket_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    generation: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="queued", server_default="queued"
    )
    attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    lease_owner: Mapped[str | None] = mapped_column(String(255))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    config_revision: Mapped[str] = mapped_column(String(255), nullable=False)
    root_model_config_id: Mapped[str] = mapped_column(String(255), nullable=False)
    child_model_config_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    model_digest: Mapped[str] = mapped_column(String(255), nullable=False)
    skill_digest: Mapped[str] = mapped_column(String(255), nullable=False)
    tool_digest: Mapped[str] = mapped_column(String(255), nullable=False)
    dead_letter_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint("ticket_id > 0", name="ck_case_jobs_ticket_id_positive"),
        CheckConstraint("generation >= 1", name="ck_case_jobs_generation_positive"),
        CheckConstraint("attempts >= 0", name="ck_case_jobs_attempts_nonnegative"),
        CheckConstraint(
            "status IN ('queued', 'claimed', 'sandbox_creating', 'running', "
            "'finalizing', 'completed', 'awaiting_customer', 'human_takeover', "
            "'failed', 'cancelled')",
            name="ck_case_jobs_status",
        ),
        Index(
            "uq_case_jobs_one_queued_ticket",
            "ticket_id",
            unique=True,
            postgresql_where=text("status = 'queued'"),
            sqlite_where=text("status = 'queued'"),
        ),
        Index("ix_case_jobs_claimable", "status", "available_at", "created_at"),
        Index("ix_case_jobs_ticket_generation", "ticket_id", "generation"),
    )


class OutboxMessage(Base):
    """Controller-owned customer reply with a generation cancellation fence."""

    __tablename__ = "outbox_messages"

    outbox_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    job_id: Mapped[str | None] = mapped_column(
        String(255), ForeignKey("case_jobs.job_id")
    )

    ticket_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    generation: Mapped[int] = mapped_column(BigInteger, nullable=False)
    snapshot_cursor: Mapped[str] = mapped_column(String(255), nullable=False)
    locale: Mapped[str] = mapped_column(String(32), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(
        String(512), nullable=False, unique=True
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="pending", server_default="pending"
    )
    provider_message_id: Mapped[str | None] = mapped_column(String(255))
    attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint("ticket_id > 0", name="ck_outbox_ticket_id_positive"),
        CheckConstraint("generation >= 1", name="ck_outbox_generation_positive"),
        CheckConstraint("attempt_count >= 0", name="ck_outbox_attempts_nonnegative"),
        CheckConstraint(
            "status IN ('pending', 'attempted', 'sent', 'confirmed', "
            "'ambiguous', 'cancelled')",
            name="ck_outbox_status",
        ),
        UniqueConstraint(
            "ticket_id",
            "generation",
            "snapshot_cursor",
            name="uq_outbox_ticket_generation_cursor",
        ),
        Index("ix_outbox_claimable", "status", "created_at"),
        Index("ix_outbox_ticket_generation", "ticket_id", "generation"),
    )


class RunRecordRow(Base):
    """Bounded and queryable projection of one finalized sandbox run."""

    __tablename__ = "run_records"

    session_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    job_id: Mapped[str] = mapped_column(
        String(255), ForeignKey("case_jobs.job_id"), nullable=False, unique=True
    )
    ticket_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    generation: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    telemetry_version: Mapped[str | None] = mapped_column(String(64))
    active_question: Mapped[dict[str, object] | None] = mapped_column(JSON)
    todo_total: Mapped[int | None] = mapped_column(Integer)
    todo_completed: Mapped[int | None] = mapped_column(Integer)
    todo_statuses: Mapped[list[dict[str, object]] | None] = mapped_column(JSON)
    tool_calls: Mapped[int | None] = mapped_column(Integer)
    tool_call_records: Mapped[list[dict[str, object]] | None] = mapped_column(JSON)
    subagents: Mapped[int | None] = mapped_column(Integer)
    subagent_tasks: Mapped[list[dict[str, object]] | None] = mapped_column(JSON)
    evidence_count: Mapped[int | None] = mapped_column(Integer)
    evidence_ids: Mapped[list[str] | None] = mapped_column(JSON)
    context_checkpoint: Mapped[dict[str, object] | None] = mapped_column(JSON)
    sandbox_lifecycle: Mapped[list[dict[str, object]] | None] = mapped_column(JSON)
    outcome: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint("ticket_id > 0", name="ck_run_records_ticket_id_positive"),
        CheckConstraint("generation >= 1", name="ck_run_records_generation_positive"),
        CheckConstraint(
            "status IN ('completed', 'awaiting_customer', 'human_takeover', "
            "'failed', 'cancelled')",
            name="ck_run_records_status",
        ),
        CheckConstraint(
            "todo_total IS NULL OR todo_total >= 0",
            name="ck_run_records_todo_total_nonnegative",
        ),
        CheckConstraint(
            "todo_completed IS NULL OR todo_completed >= 0",
            name="ck_run_records_todo_completed_nonnegative",
        ),
        CheckConstraint(
            "tool_calls IS NULL OR tool_calls >= 0",
            name="ck_run_records_tool_calls_nonnegative",
        ),
        CheckConstraint(
            "subagents IS NULL OR subagents >= 0",
            name="ck_run_records_subagents_nonnegative",
        ),
        CheckConstraint(
            "evidence_count IS NULL OR evidence_count >= 0",
            name="ck_run_records_evidence_count_nonnegative",
        ),
        Index("ix_run_records_ticket_generation", "ticket_id", "generation"),
        Index("ix_run_records_finished_at", "finished_at"),
    )


class RunEventRow(Base):
    """Append-only lifecycle event for the bounded run projection."""

    __tablename__ = "run_events"

    event_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        String(255), ForeignKey("run_records.session_id"), nullable=False
    )
    job_id: Mapped[str] = mapped_column(
        String(255), ForeignKey("case_jobs.job_id"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("ix_run_events_session_occurred", "session_id", "occurred_at"),
        Index("ix_run_events_job_occurred", "job_id", "occurred_at"),
    )


__all__ = [
    "ACTIVE_JOB_STATUSES",
    "Base",
    "CaseJobRow",
    "ConfigHeadRow",
    "ConfigRevisionRow",
    "EventRecord",
    "OutboxMessage",
    "RunEventRow",
    "RunRecordRow",
    "TicketState",
]
