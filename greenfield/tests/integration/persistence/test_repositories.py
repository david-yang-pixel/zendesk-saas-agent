from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import Result
from sqlalchemy.orm import Session

from casework_persistence.models import Base, CaseJobRow, OutboxMessage
from casework_persistence.repositories import (
    ConfigSnapshot,
    EventRepository,
    HumanCancelRepository,
    JobRepository,
    OutboxDraft,
    OutboxRepository,
    OutboxStatus,
)

NOW = datetime(2026, 9, 1, tzinfo=UTC)
CONFIG = ConfigSnapshot(
    config_revision="cfg-1",
    root_model_config_id="root-1",
    child_model_config_ids=("logs-1", "db-1"),
    model_digest="sha256:model",
    skill_digest="sha256:skills",
    tool_digest="sha256:tools",
)


class AsyncSessionShim:
    """Tiny async facade over SQLite for repository contract tests.

    PostgreSQL-specific locking is compiled by the production repository; this
    shim exercises idempotency and generation behavior without requiring a
    running database in every developer checkout.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    async def execute(self, statement: Any) -> Result[Any]:
        return self._session.execute(statement)

    def get(self, model: type[Any], key: Any) -> Any | None:
        return self._session.get(model, key)


@pytest.fixture
def db_session() -> Iterator[AsyncSessionShim]:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield AsyncSessionShim(session)
        session.commit()


@pytest.mark.asyncio
async def test_event_repository_deduplicates_event_id(
    db_session: AsyncSessionShim,
) -> None:
    events = EventRepository(db_session)
    first = await events.record(
        event_id="evt-1",
        ticket_id=3726,
        occurred_at=NOW,
        source="zendesk",
        customer_origin="customer",
        payload={"status": "open"},
    )
    second = await events.record(
        event_id="evt-1",
        ticket_id=3726,
        occurred_at=NOW,
        source="zendesk",
        customer_origin="customer",
        payload={"status": "open"},
    )
    assert first is True
    assert second is False


@pytest.mark.asyncio
async def test_enqueue_allows_only_one_active_job_per_ticket(
    db_session: AsyncSessionShim,
) -> None:
    events = EventRepository(db_session)
    await events.record(
        event_id="evt-1",
        ticket_id=3726,
        occurred_at=NOW,
        source="zendesk",
        customer_origin="customer",
    )
    jobs = JobRepository(db_session)
    first = await jobs.enqueue(
        ticket_id=3726, event_id="evt-1", config=CONFIG, job_id="job-1"
    )
    await events.record(
        event_id="evt-2",
        ticket_id=3726,
        occurred_at=NOW + timedelta(seconds=1),
        source="zendesk",
        customer_origin="customer",
    )
    second = await jobs.enqueue(
        ticket_id=3726, event_id="evt-2", config=CONFIG, job_id="job-2"
    )
    assert first.accepted is True
    assert second.accepted is False
    assert second.reason == "active_ticket_job"


@pytest.mark.asyncio
async def test_claim_and_heartbeat_use_a_lease_and_reclaim_expired_claim(
    db_session: AsyncSessionShim,
) -> None:
    events = EventRepository(db_session)
    await events.record(
        event_id="evt-claim",
        ticket_id=3726,
        occurred_at=NOW,
        source="zendesk",
        customer_origin="customer",
    )
    jobs = JobRepository(db_session)
    await jobs.enqueue(
        ticket_id=3726,
        event_id="evt-claim",
        config=CONFIG,
        job_id="job-claim",
        available_at=NOW,
    )
    claimed = await jobs.claim_next(worker_id="worker-a", lease_seconds=30, now=NOW)
    assert claimed is not None
    assert claimed.job_id == "job-claim"
    assert claimed.status == "claimed"
    assert claimed.attempts == 1
    assert claimed.lease_owner == "worker-a"
    assert await jobs.heartbeat(
        job_id="job-claim", worker_id="worker-a", lease_seconds=30, now=NOW
    )
    assert not await jobs.heartbeat(
        job_id="job-claim", worker_id="wrong-worker", lease_seconds=30, now=NOW
    )
    reclaimed = await jobs.claim_next(
        worker_id="worker-b", lease_seconds=30, now=NOW + timedelta(seconds=31)
    )
    assert reclaimed is not None
    assert reclaimed.lease_owner == "worker-b"
    assert reclaimed.attempts == 2


@pytest.mark.asyncio
async def test_expired_job_at_retry_limit_is_dead_lettered(
    db_session: AsyncSessionShim,
) -> None:
    events = EventRepository(db_session)
    await events.record(
        event_id="evt-dead",
        ticket_id=3727,
        occurred_at=NOW,
        source="zendesk",
        customer_origin="customer",
    )
    jobs = JobRepository(db_session)
    await jobs.enqueue(
        ticket_id=3727,
        event_id="evt-dead",
        config=CONFIG,
        job_id="job-dead",
        available_at=NOW,
    )
    claimed = await jobs.claim_next(
        worker_id="worker-a", lease_seconds=1, now=NOW, max_attempts=1
    )
    assert claimed is not None
    assert not await jobs.claim_next(
        worker_id="worker-b",
        lease_seconds=1,
        now=NOW + timedelta(seconds=2),
        max_attempts=1,
    )
    job = await jobs.get("job-dead")
    assert job is not None
    assert job.status == "failed"
    assert job.dead_letter_reason == "max_attempts_exceeded"


@pytest.mark.asyncio
async def test_human_cancel_advances_generation_and_fences_jobs_and_outbox(
    db_session: AsyncSessionShim,
) -> None:
    events = EventRepository(db_session)
    await events.record(
        event_id="evt-1",
        ticket_id=3726,
        occurred_at=NOW,
        source="zendesk",
        customer_origin="customer",
    )
    jobs = JobRepository(db_session)
    await jobs.enqueue(ticket_id=3726, event_id="evt-1", config=CONFIG, job_id="job-1")
    outbox = OutboxRepository(db_session)
    await outbox.create(
        OutboxDraft(
            outbox_id="out-1",
            job_id="job-1",
            ticket_id=3726,
            generation=0,
            snapshot_cursor="cursor-1",
            locale="zh-CN",
            body="reply",
            idempotency_key="reply:3726:0:cursor-1",
        )
    )
    cancel = HumanCancelRepository(db_session)
    result = await cancel.apply(
        event_id="evt-human-1",
        ticket_id=3726,
        occurred_at=NOW + timedelta(seconds=2),
        reason="customer requested a human",
    )
    assert result.generation == 1
    assert result.cancelled_jobs == 1
    assert result.cancelled_outbox == 1
    job = cast(CaseJobRow, db_session.get(CaseJobRow, "job-1"))
    message = cast(OutboxMessage, db_session.get(OutboxMessage, "out-1"))
    assert job is not None
    assert message is not None
    assert job.status == "cancelled"
    assert message.status == OutboxStatus.CANCELLED


@pytest.mark.asyncio
async def test_outbox_create_is_idempotent_by_key(
    db_session: AsyncSessionShim,
) -> None:
    events = EventRepository(db_session)
    await events.record(
        event_id="evt-1",
        ticket_id=3726,
        occurred_at=NOW,
        source="zendesk",
        customer_origin="customer",
    )
    jobs = JobRepository(db_session)
    await jobs.enqueue(ticket_id=3726, event_id="evt-1", config=CONFIG, job_id="job-1")
    outbox = OutboxRepository(db_session)
    draft = OutboxDraft(
        outbox_id="out-1",
        job_id="job-1",
        ticket_id=3726,
        generation=0,
        snapshot_cursor="cursor-1",
        locale="ja-JP",
        body="reply",
        idempotency_key="reply:3726:0:cursor-1",
    )
    first = await outbox.create(draft)
    second = await outbox.create(draft)
    assert first.outbox_id == second.outbox_id == "out-1"
