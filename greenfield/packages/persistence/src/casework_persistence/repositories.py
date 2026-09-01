"""Async PostgreSQL repositories for durable Casework state.

Methods intentionally do not commit.  A webhook or worker transaction owns the
commit boundary so the event, queue row, state transition and outbox fence can
be committed atomically.  ``AsyncSession`` is injected, which also keeps the
repository straightforward to exercise with a PostgreSQL test database.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Protocol, cast
from uuid import uuid4

from sqlalchemy import and_, insert, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import CursorResult, Result
from sqlalchemy.ext.asyncio import AsyncSession

from casework_persistence.models import (
    ACTIVE_JOB_STATUSES,
    Base,
    CaseJobRow,
    EventRecord,
    OutboxMessage,
    TicketState,
)


def utc_now() -> datetime:
    return datetime.now(UTC)


def _rowcount(result: Result[Any]) -> int:
    """Read DML row counts without weakening the async session port type."""

    return int(cast(CursorResult[Any], result).rowcount or 0)


class OutboxStatus(StrEnum):
    PENDING = "pending"
    ATTEMPTED = "attempted"
    SENT = "sent"
    CONFIRMED = "confirmed"
    AMBIGUOUS = "ambiguous"
    CANCELLED = "cancelled"


class GenerationFenceError(RuntimeError):
    """Raised when work belongs to an older ticket generation."""


class MissingConfigSnapshotError(ValueError):
    """Raised before a job can enter the durable queue without frozen config."""


class SessionPort(Protocol):
    """Minimal async session surface used by repositories and test fakes."""

    async def execute(self, statement: Any) -> Result[Any]: ...


@dataclass(frozen=True, slots=True)
class ConfigSnapshot:
    """Configuration identity frozen onto every root run and child session."""

    config_revision: str
    root_model_config_id: str
    child_model_config_ids: tuple[str, ...]
    model_digest: str
    skill_digest: str
    tool_digest: str

    def __post_init__(self) -> None:
        fields = (
            self.config_revision,
            self.root_model_config_id,
            self.model_digest,
            self.skill_digest,
            self.tool_digest,
        )
        if any(not value.strip() for value in fields):
            raise MissingConfigSnapshotError(
                "config revision, root model, and registry digests are required"
            )
        if any(not value.strip() for value in self.child_model_config_ids):
            raise MissingConfigSnapshotError(
                "child model configuration identifiers must not be blank"
            )
        if len(set(self.child_model_config_ids)) != len(self.child_model_config_ids):
            raise MissingConfigSnapshotError(
                "child model configuration identifiers must be unique"
            )


@dataclass(frozen=True, slots=True)
class EnqueueResult:
    accepted: bool
    job_id: str | None
    ticket_id: int
    generation: int
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class ClaimResult:
    job: CaseJobRow | None


@dataclass(frozen=True, slots=True)
class CancelResult:
    event_id: str
    ticket_id: int
    generation: int
    cancelled_jobs: int
    cancelled_outbox: int
    duplicate_event: bool = False


@dataclass(frozen=True, slots=True)
class OutboxDraft:
    outbox_id: str
    job_id: str | None
    ticket_id: int
    generation: int
    snapshot_cursor: str
    locale: str
    body: str
    idempotency_key: str


@asynccontextmanager
async def session_transaction(
    session: AsyncSession,
) -> AsyncGenerator[AsyncSession, None]:
    """Provide an explicit transaction for service composition and tests."""

    async with session.begin():
        yield session


class EventRepository:
    """Persist normalized webhook events exactly once by event ID."""

    def __init__(self, session: SessionPort) -> None:
        self.session = session

    async def record(
        self,
        *,
        event_id: str,
        ticket_id: int,
        occurred_at: datetime,
        source: str,
        customer_origin: str,
        event_type: str = "ticket.updated",
        message_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> bool:
        """Return true only when this event ID was newly inserted."""

        if ticket_id <= 0:
            raise ValueError("ticket_id must be positive")
        if occurred_at.tzinfo is None or occurred_at.utcoffset() is None:
            raise ValueError("occurred_at must be timezone-aware")
        for field_name, value in {
            "event_id": event_id,
            "source": source,
            "customer_origin": customer_origin,
            "event_type": event_type,
        }.items():
            if not value.strip():
                raise ValueError(f"{field_name} must not be blank")

        statement = pg_insert(EventRecord).values(
            event_id=event_id,
            ticket_id=ticket_id,
            occurred_at=occurred_at,
            source=source,
            customer_origin=customer_origin,
            event_type=event_type,
            message_id=message_id,
            payload=payload or {},
        )
        statement = statement.on_conflict_do_nothing(
            index_elements=[EventRecord.event_id]
        )
        result = await self.session.execute(statement)
        return _rowcount(result) > 0

    async def exists(self, event_id: str) -> bool:
        result = await self.session.execute(
            select(EventRecord.event_id).where(EventRecord.event_id == event_id)
        )
        return result.scalar_one_or_none() is not None


class TicketRepository:
    """Generation and human-cancellation fence operations."""

    def __init__(self, session: SessionPort) -> None:
        self.session = session

    async def ensure_state(self, ticket_id: int) -> TicketState:
        """Create and lock a ticket state row without a read-before-insert race."""

        statement = pg_insert(TicketState).values(ticket_id=ticket_id)
        statement = statement.on_conflict_do_nothing(
            index_elements=[TicketState.ticket_id]
        )
        await self.session.execute(statement)
        result = await self.session.execute(
            select(TicketState)
            .where(TicketState.ticket_id == ticket_id)
            .with_for_update()
        )
        state = result.scalar_one()
        return state

    async def current_generation(self, ticket_id: int) -> int:
        result = await self.session.execute(
            select(TicketState.generation).where(TicketState.ticket_id == ticket_id)
        )
        generation = result.scalar_one_or_none()
        return int(generation) if generation is not None else 0


class JobRepository:
    """Atomic claim, lease and generation-aware queue operations."""

    def __init__(self, session: SessionPort) -> None:
        self.session = session
        self.events = EventRepository(session)
        self.tickets = TicketRepository(session)

    async def get(self, job_id: str) -> CaseJobRow | None:
        """Read one job with fresh values after a bulk state transition."""

        result = await self.session.execute(
            select(CaseJobRow)
            .where(CaseJobRow.job_id == job_id)
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def enqueue(
        self,
        *,
        ticket_id: int,
        event_id: str,
        config: ConfigSnapshot,
        available_at: datetime | None = None,
        job_id: str | None = None,
    ) -> EnqueueResult:
        """Create at most one active job for the ticket's current generation."""

        if ticket_id <= 0:
            raise ValueError("ticket_id must be positive")
        if not event_id.strip():
            raise ValueError("event_id must not be blank")

        # Reconstruct to apply the invariant checks even when a caller passes
        # a ConfigSnapshot subclass or values reconstructed from JSON.
        config = ConfigSnapshot(
            config_revision=config.config_revision,
            root_model_config_id=config.root_model_config_id,
            child_model_config_ids=tuple(config.child_model_config_ids),
            model_digest=config.model_digest,
            skill_digest=config.skill_digest,
            tool_digest=config.tool_digest,
        )
        state = await self.tickets.ensure_state(ticket_id)
        generation = state.generation
        existing = await self.session.execute(
            select(CaseJobRow).where(CaseJobRow.event_id == event_id)
        )
        duplicate = existing.scalar_one_or_none()
        if duplicate is not None:
            return EnqueueResult(
                accepted=False,
                job_id=duplicate.job_id,
                ticket_id=ticket_id,
                generation=duplicate.generation,
                reason="duplicate_event",
            )

        active = await self.session.execute(
            select(CaseJobRow.job_id)
            .where(
                CaseJobRow.ticket_id == ticket_id,
                CaseJobRow.status.in_(ACTIVE_JOB_STATUSES),
            )
            .limit(1)
        )
        active_id = active.scalar_one_or_none()
        if active_id is not None:
            return EnqueueResult(
                accepted=False,
                job_id=active_id,
                ticket_id=ticket_id,
                generation=generation,
                reason="active_ticket_job",
            )

        new_job_id = job_id or f"job-{uuid4()}"
        await self.session.execute(
            insert(CaseJobRow).values(
                job_id=new_job_id,
                event_id=event_id,
                ticket_id=ticket_id,
                generation=generation,
                status="queued",
                attempts=0,
                available_at=available_at or utc_now(),
                config_revision=config.config_revision,
                root_model_config_id=config.root_model_config_id,
                child_model_config_ids=list(config.child_model_config_ids),
                model_digest=config.model_digest,
                skill_digest=config.skill_digest,
                tool_digest=config.tool_digest,
            )
        )
        return EnqueueResult(
            accepted=True,
            job_id=new_job_id,
            ticket_id=ticket_id,
            generation=generation,
        )

    async def claim_next(
        self,
        *,
        worker_id: str,
        lease_seconds: int = 60,
        max_attempts: int = 5,
        now: datetime | None = None,
    ) -> CaseJobRow | None:
        """Claim one queued or lease-expired job using SKIP LOCKED."""

        claimed_at = now or utc_now()
        if lease_seconds <= 0 or max_attempts <= 0:
            raise ValueError("lease_seconds and max_attempts must be positive")
        lease_expires_at = claimed_at + timedelta(seconds=lease_seconds)
        await self.session.execute(
            update(CaseJobRow)
            .where(
                CaseJobRow.status.in_(
                    {"claimed", "sandbox_creating", "running", "finalizing"}
                ),
                CaseJobRow.lease_expires_at <= claimed_at,
                CaseJobRow.attempts >= max_attempts,
            )
            .values(
                status="failed",
                lease_owner=None,
                lease_expires_at=None,
                heartbeat_at=None,
                dead_letter_reason="max_attempts_exceeded",
                finished_at=claimed_at,
                updated_at=claimed_at,
            )
            .execution_options(synchronize_session=False)
        )
        claimable = and_(
            or_(
                and_(
                    CaseJobRow.status == "queued",
                    CaseJobRow.available_at <= claimed_at,
                ),
                and_(
                    CaseJobRow.status.in_(
                        {"claimed", "sandbox_creating", "running", "finalizing"}
                    ),
                    CaseJobRow.lease_expires_at <= claimed_at,
                    CaseJobRow.attempts < max_attempts,
                ),
            ),
        )
        next_job = (
            select(CaseJobRow.job_id)
            .where(claimable)
            .order_by(CaseJobRow.created_at, CaseJobRow.job_id)
            .with_for_update(skip_locked=True)
            .limit(1)
            .scalar_subquery()
        )
        statement = (
            update(CaseJobRow)
            .where(CaseJobRow.job_id == next_job)
            .values(
                status="claimed",
                lease_owner=worker_id,
                lease_expires_at=lease_expires_at,
                heartbeat_at=claimed_at,
                attempts=CaseJobRow.attempts + 1,
                updated_at=claimed_at,
            )
            .returning(CaseJobRow)
        )
        result = await self.session.execute(statement)
        return result.scalars().first()

    async def heartbeat(
        self,
        *,
        job_id: str,
        worker_id: str,
        lease_seconds: int = 60,
        now: datetime | None = None,
    ) -> bool:
        timestamp = now or utc_now()
        result = await self.session.execute(
            update(CaseJobRow)
            .where(
                CaseJobRow.job_id == job_id,
                CaseJobRow.lease_owner == worker_id,
                CaseJobRow.status.in_(ACTIVE_JOB_STATUSES),
            )
            .values(
                heartbeat_at=timestamp,
                lease_expires_at=timestamp + timedelta(seconds=lease_seconds),
                updated_at=timestamp,
            )
        )
        return _rowcount(result) > 0

    async def transition(
        self,
        *,
        job_id: str,
        worker_id: str,
        status: str,
        now: datetime | None = None,
    ) -> bool:
        timestamp = now or utc_now()
        if status not in {
            "queued",
            "claimed",
            "sandbox_creating",
            "running",
            "finalizing",
            "completed",
            "awaiting_customer",
            "human_takeover",
            "failed",
            "cancelled",
        }:
            raise ValueError(f"unknown job status: {status}")
        result = await self.session.execute(
            update(CaseJobRow)
            .where(
                CaseJobRow.job_id == job_id,
                CaseJobRow.lease_owner == worker_id,
                CaseJobRow.status.in_(ACTIVE_JOB_STATUSES),
            )
            .values(status=status, updated_at=timestamp)
        )
        return _rowcount(result) > 0

    async def retry(
        self,
        *,
        job_id: str,
        worker_id: str,
        delay_seconds: int,
        reason: str,
        now: datetime | None = None,
    ) -> bool:
        """Return a leased job to the queue with caller-selected backoff."""

        if delay_seconds < 0 or not reason.strip():
            raise ValueError("delay_seconds must be nonnegative and reason required")
        timestamp = now or utc_now()
        result = await self.session.execute(
            update(CaseJobRow)
            .where(
                CaseJobRow.job_id == job_id,
                CaseJobRow.lease_owner == worker_id,
                CaseJobRow.status.in_(ACTIVE_JOB_STATUSES),
            )
            .values(
                status="queued",
                available_at=timestamp + timedelta(seconds=delay_seconds),
                lease_owner=None,
                lease_expires_at=None,
                heartbeat_at=None,
                dead_letter_reason=reason,
                updated_at=timestamp,
            )
        )
        return _rowcount(result) > 0

    async def finish(
        self,
        *,
        job_id: str,
        worker_id: str,
        status: str,
        dead_letter_reason: str | None = None,
        now: datetime | None = None,
    ) -> bool:
        timestamp = now or utc_now()
        result = await self.session.execute(
            update(CaseJobRow)
            .where(
                CaseJobRow.job_id == job_id,
                CaseJobRow.lease_owner == worker_id,
                CaseJobRow.status.in_(ACTIVE_JOB_STATUSES),
            )
            .values(
                status=status,
                lease_owner=None,
                lease_expires_at=None,
                heartbeat_at=None,
                dead_letter_reason=dead_letter_reason,
                finished_at=timestamp,
                updated_at=timestamp,
            )
        )
        return _rowcount(result) > 0


class HumanCancelRepository:
    """Apply a customer ``human`` command and fence all older work atomically."""

    def __init__(self, session: SessionPort) -> None:
        self.session = session
        self.events = EventRepository(session)
        self.tickets = TicketRepository(session)

    async def apply(
        self,
        *,
        event_id: str,
        ticket_id: int,
        occurred_at: datetime,
        reason: str,
        source: str = "zendesk",
        payload: dict[str, Any] | None = None,
    ) -> CancelResult:
        if ticket_id <= 0:
            raise ValueError("ticket_id must be positive")
        if occurred_at.tzinfo is None or occurred_at.utcoffset() is None:
            raise ValueError("occurred_at must be timezone-aware")
        if not event_id.strip() or not reason.strip():
            raise ValueError("event_id and reason must not be blank")
        inserted = await self.events.record(
            event_id=event_id,
            ticket_id=ticket_id,
            occurred_at=occurred_at,
            source=source,
            customer_origin="customer",
            event_type="human.cancel",
            payload=payload or {"reason": reason},
        )
        state = await self.tickets.ensure_state(ticket_id)
        if not inserted:
            return CancelResult(
                event_id=event_id,
                ticket_id=ticket_id,
                generation=state.generation,
                cancelled_jobs=0,
                cancelled_outbox=0,
                duplicate_event=True,
            )

        new_generation = state.generation + 1
        timestamp = occurred_at
        state.generation = new_generation
        state.last_event_id = event_id
        state.human_cancelled_at = timestamp
        state.updated_at = timestamp

        jobs = await self.session.execute(
            update(CaseJobRow)
            .where(
                CaseJobRow.ticket_id == ticket_id,
                CaseJobRow.generation < new_generation,
                CaseJobRow.status.in_(ACTIVE_JOB_STATUSES),
            )
            .values(
                status="cancelled",
                lease_owner=None,
                lease_expires_at=None,
                heartbeat_at=None,
                updated_at=timestamp,
                finished_at=timestamp,
                dead_letter_reason="human_takeover",
            )
        )
        outbox = await self.session.execute(
            update(OutboxMessage)
            .where(
                OutboxMessage.ticket_id == ticket_id,
                OutboxMessage.generation < new_generation,
                OutboxMessage.status.in_(
                    (
                        OutboxStatus.PENDING,
                        OutboxStatus.ATTEMPTED,
                        OutboxStatus.AMBIGUOUS,
                    )
                ),
            )
            .values(
                status=OutboxStatus.CANCELLED,
                updated_at=timestamp,
                last_error="human_takeover",
            )
        )
        return CancelResult(
            event_id=event_id,
            ticket_id=ticket_id,
            generation=new_generation,
            cancelled_jobs=_rowcount(jobs),
            cancelled_outbox=_rowcount(outbox),
        )


class OutboxRepository:
    """Transactional reply drafts and idempotent provider-delivery state."""

    def __init__(self, session: SessionPort) -> None:
        self.session = session

    async def create(self, draft: OutboxDraft) -> OutboxMessage:
        if draft.ticket_id <= 0 or draft.generation < 0:
            raise ValueError("ticket_id must be positive and generation nonnegative")
        if any(
            not value.strip()
            for value in (
                draft.outbox_id,
                draft.snapshot_cursor,
                draft.locale,
                draft.body,
                draft.idempotency_key,
            )
        ):
            raise ValueError("outbox identifiers and body must not be blank")
        state = await self.session.execute(
            select(TicketState.generation).where(
                TicketState.ticket_id == draft.ticket_id
            )
        )
        current_generation = state.scalar_one_or_none()
        if current_generation is not None and current_generation != draft.generation:
            raise GenerationFenceError(
                f"ticket {draft.ticket_id} is at generation {current_generation}, "
                f"draft is {draft.generation}"
            )
        existing = await self.session.execute(
            select(OutboxMessage).where(
                OutboxMessage.idempotency_key == draft.idempotency_key
            )
        )
        row = existing.scalar_one_or_none()
        if row is not None:
            return row
        await self.session.execute(
            pg_insert(OutboxMessage)
            .values(
                outbox_id=draft.outbox_id,
                job_id=draft.job_id,
                ticket_id=draft.ticket_id,
                generation=draft.generation,
                snapshot_cursor=draft.snapshot_cursor,
                locale=draft.locale,
                body=draft.body,
                idempotency_key=draft.idempotency_key,
                status=OutboxStatus.PENDING,
                attempt_count=0,
            )
            .on_conflict_do_nothing()
        )
        created = await self.session.execute(
            select(OutboxMessage).where(
                or_(
                    OutboxMessage.idempotency_key == draft.idempotency_key,
                    and_(
                        OutboxMessage.ticket_id == draft.ticket_id,
                        OutboxMessage.generation == draft.generation,
                        OutboxMessage.snapshot_cursor == draft.snapshot_cursor,
                    ),
                )
            )
        )
        return created.scalar_one()

    async def claim_pending(
        self,
        *,
        worker_id: str,
        now: datetime | None = None,
    ) -> OutboxMessage | None:
        timestamp = now or utc_now()
        next_message = (
            select(OutboxMessage.outbox_id)
            .join(
                TicketState,
                and_(
                    TicketState.ticket_id == OutboxMessage.ticket_id,
                    TicketState.generation == OutboxMessage.generation,
                ),
            )
            .where(OutboxMessage.status == OutboxStatus.PENDING)
            .order_by(OutboxMessage.created_at, OutboxMessage.outbox_id)
            .with_for_update(skip_locked=True)
            .limit(1)
            .scalar_subquery()
        )
        # Delivery ownership is represented by the provider attempt itself;
        # worker_id is retained in last_error-free metadata only in the worker.
        statement = (
            update(OutboxMessage)
            .where(OutboxMessage.outbox_id == next_message)
            .values(
                status=OutboxStatus.ATTEMPTED,
                attempt_count=OutboxMessage.attempt_count + 1,
                updated_at=timestamp,
            )
            .returning(OutboxMessage)
        )
        result = await self.session.execute(statement)
        return result.scalars().first()

    async def mark_sent(
        self,
        *,
        outbox_id: str,
        provider_message_id: str,
        now: datetime | None = None,
    ) -> bool:
        result = await self.session.execute(
            update(OutboxMessage)
            .where(
                OutboxMessage.outbox_id == outbox_id,
                OutboxMessage.status == OutboxStatus.ATTEMPTED,
            )
            .values(
                status=OutboxStatus.SENT,
                provider_message_id=provider_message_id,
                updated_at=now or utc_now(),
            )
        )
        return _rowcount(result) > 0

    async def mark_confirmed(
        self,
        *,
        outbox_id: str,
        provider_message_id: str | None = None,
        now: datetime | None = None,
    ) -> bool:
        result = await self.session.execute(
            update(OutboxMessage)
            .where(
                OutboxMessage.outbox_id == outbox_id,
                OutboxMessage.status.in_((OutboxStatus.SENT, OutboxStatus.ATTEMPTED)),
            )
            .values(
                status=OutboxStatus.CONFIRMED,
                provider_message_id=provider_message_id,
                updated_at=now or utc_now(),
            )
        )
        return _rowcount(result) > 0

    async def mark_ambiguous(
        self,
        *,
        outbox_id: str,
        error: str,
        now: datetime | None = None,
    ) -> bool:
        result = await self.session.execute(
            update(OutboxMessage)
            .where(
                OutboxMessage.outbox_id == outbox_id,
                OutboxMessage.status == OutboxStatus.ATTEMPTED,
            )
            .values(
                status=OutboxStatus.AMBIGUOUS,
                last_error=error,
                updated_at=now or utc_now(),
            )
        )
        return _rowcount(result) > 0

    async def fence_ticket(self, *, ticket_id: int, generation: int) -> int:
        result = await self.session.execute(
            update(OutboxMessage)
            .where(
                OutboxMessage.ticket_id == ticket_id,
                OutboxMessage.generation < generation,
                OutboxMessage.status.in_(
                    (
                        OutboxStatus.PENDING,
                        OutboxStatus.ATTEMPTED,
                        OutboxStatus.AMBIGUOUS,
                    )
                ),
            )
            .values(status=OutboxStatus.CANCELLED, last_error="generation_fence")
        )
        return _rowcount(result)


class CaseworkPersistence:
    """Convenience aggregate exposing all repository ports for one session."""

    def __init__(self, session: SessionPort) -> None:
        self.events = EventRepository(session)
        self.tickets = TicketRepository(session)
        self.jobs = JobRepository(session)
        self.human_cancel = HumanCancelRepository(session)
        self.outbox = OutboxRepository(session)


__all__ = [
    "Base",
    "CancelResult",
    "CaseworkPersistence",
    "ClaimResult",
    "ConfigSnapshot",
    "EnqueueResult",
    "EventRepository",
    "GenerationFenceError",
    "HumanCancelRepository",
    "JobRepository",
    "MissingConfigSnapshotError",
    "OutboxDraft",
    "OutboxRepository",
    "OutboxStatus",
    "SessionPort",
    "TicketRepository",
    "session_transaction",
]
