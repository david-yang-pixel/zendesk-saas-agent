from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import cast

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from casework_persistence.models import (
    CaseJobRow,
    EventRecord,
    OutboxMessage,
    TicketState,
)
from casework_persistence.repositories import (
    ConfigRevisionConflict,
    ConfigRevisionRepository,
    ConfigSnapshot,
    EventRepository,
    GenerationFenceError,
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


async def _seed_job(
    sessions: async_sessionmaker[AsyncSession],
    *,
    event_id: str,
    ticket_id: int,
    job_id: str,
    available_at: datetime = NOW,
) -> None:
    async with sessions() as session, session.begin():
        await EventRepository(session).record(
            event_id=event_id,
            ticket_id=ticket_id,
            occurred_at=available_at,
            source="zendesk",
            customer_origin="customer",
        )
        await JobRepository(session).enqueue(
            ticket_id=ticket_id,
            event_id=event_id,
            config=CONFIG,
            job_id=job_id,
            available_at=available_at,
            created_at=available_at,
        )


async def _rows(
    sessions: async_sessionmaker[AsyncSession], model: type[object]
) -> list[object]:
    async with sessions() as session:
        result = await session.execute(select(model))
        return list(result.scalars().all())


@pytest.mark.asyncio
async def test_atomic_event_to_job_rolls_back_together(
    postgres_sessions: async_sessionmaker[AsyncSession],
) -> None:
    with pytest.raises(RuntimeError, match="rollback admission"):
        async with postgres_sessions() as session, session.begin():
            await EventRepository(session).record(
                event_id="evt-rollback",
                ticket_id=4100,
                occurred_at=NOW,
                source="zendesk",
                customer_origin="customer",
            )
            await JobRepository(session).enqueue(
                ticket_id=4100,
                event_id="evt-rollback",
                config=CONFIG,
                job_id="job-rollback",
                available_at=NOW,
            )
            raise RuntimeError("rollback admission")

    assert await _rows(postgres_sessions, EventRecord) == []
    assert await _rows(postgres_sessions, CaseJobRow) == []
    assert await _rows(postgres_sessions, TicketState) == []


@pytest.mark.asyncio
async def test_postgres_skip_locked_claim_skips_a_locked_earlier_job(
    postgres_sessions: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_job(
        postgres_sessions,
        event_id="evt-a",
        ticket_id=4101,
        job_id="job-a",
        available_at=NOW,
    )
    await _seed_job(
        postgres_sessions,
        event_id="evt-b",
        ticket_id=4102,
        job_id="job-b",
        available_at=NOW + timedelta(seconds=1),
    )

    async with postgres_sessions() as locker:
        async with locker.begin():
            await locker.execute(
                select(CaseJobRow)
                .where(CaseJobRow.job_id == "job-a")
                .with_for_update()
            )
            async with postgres_sessions() as claimer, claimer.begin():
                claimed = await JobRepository(claimer).claim_next(
                    worker_id="worker-b", now=NOW + timedelta(seconds=2)
                )
                assert claimed is not None
                assert claimed.job_id == "job-b"


@pytest.mark.asyncio
async def test_postgres_lease_heartbeat_and_expiry_reclaim(
    postgres_sessions: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_job(
        postgres_sessions,
        event_id="evt-lease",
        ticket_id=4103,
        job_id="job-lease",
    )
    async with postgres_sessions() as session, session.begin():
        claimed = await JobRepository(session).claim_next(
            worker_id="worker-a", lease_seconds=10, now=NOW
        )
        assert claimed is not None
        assert claimed.attempts == 1

    async with postgres_sessions() as session, session.begin():
        jobs = JobRepository(session)
        assert await jobs.heartbeat(
            job_id="job-lease", worker_id="worker-a", lease_seconds=10, now=NOW
        )
        assert not await jobs.heartbeat(
            job_id="job-lease", worker_id="worker-other", now=NOW
        )

    async with postgres_sessions() as session, session.begin():
        reclaimed = await JobRepository(session).claim_next(
            worker_id="worker-b", lease_seconds=10, now=NOW + timedelta(seconds=11)
        )
        assert reclaimed is not None
        assert reclaimed.job_id == "job-lease"
        assert reclaimed.attempts == 2


@pytest.mark.asyncio
async def test_postgres_config_compare_and_swap_allows_one_concurrent_writer(
    postgres_sessions: async_sessionmaker[AsyncSession],
) -> None:
    async with postgres_sessions() as session, session.begin():
        await ConfigRevisionRepository(session).bootstrap(
            revision="cfg-bootstrap", config={"version": 1}, actor="bootstrap"
        )

    async def append(revision: str) -> str:
        try:
            async with postgres_sessions() as session, session.begin():
                await ConfigRevisionRepository(session).append(
                    expected_revision="cfg-bootstrap",
                    revision=revision,
                    config={"version": revision},
                    actor=revision,
                )
            return "success"
        except ConfigRevisionConflict:
            return "conflict"

    results = await asyncio.gather(append("cfg-a"), append("cfg-b"))
    assert sorted(results) == ["conflict", "success"]
    async with postgres_sessions() as session:
        current = await ConfigRevisionRepository(session).current()
    assert current.revision in {"cfg-a", "cfg-b"}


@pytest.mark.asyncio
async def test_postgres_human_cancel_advances_generation_and_fences_delivery(
    postgres_sessions: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_job(
        postgres_sessions,
        event_id="evt-human-job",
        ticket_id=4104,
        job_id="job-human",
    )
    async with postgres_sessions() as session, session.begin():
        await JobRepository(session).claim_next(
            worker_id="worker-human", now=NOW
        )
    draft = OutboxDraft(
        outbox_id="out-human",
        job_id="job-human",
        ticket_id=4104,
        generation=1,
        snapshot_cursor="cursor-human",
        locale="ja-JP",
        body="回答",
        idempotency_key="reply:4104:1:cursor-human",
    )
    async with postgres_sessions() as session, session.begin():
        await OutboxRepository(session).create(draft)
        attempted = await OutboxRepository(session).claim_pending(
            worker_id="delivery-worker", now=NOW
        )
        assert attempted is not None

    async with postgres_sessions() as session, session.begin():
        result = await HumanCancelRepository(session).apply(
            event_id="evt-human-cancel",
            ticket_id=4104,
            occurred_at=NOW + timedelta(seconds=1),
            reason="human",
        )
        assert result.generation == 2
        assert result.cancelled_jobs == 1
        assert result.cancelled_outbox == 1

    async with postgres_sessions() as session, session.begin():
        job = (await session.execute(select(CaseJobRow))).scalar_one()
        message = (await session.execute(select(OutboxMessage))).scalar_one()
        state = (await session.execute(select(TicketState))).scalar_one()
        assert job.status == "cancelled"
        assert message.status == OutboxStatus.CANCELLED
        assert state.generation == 2
        assert not await JobRepository(session).finish(
            job_id="job-human", worker_id="worker-human", status="completed"
        )
        assert not await OutboxRepository(session).mark_sent(
            outbox_id="out-human", provider_message_id="late-message"
        )
        with pytest.raises(GenerationFenceError):
            await OutboxRepository(session).create(draft)

    await _seed_job(
        postgres_sessions,
        event_id="evt-human-new",
        ticket_id=4104,
        job_id="job-human-new",
    )
    async with postgres_sessions() as session:
        row = (
            await session.execute(
                select(CaseJobRow).where(CaseJobRow.job_id == "job-human-new")
            )
        ).scalar_one()
        assert row.generation == 2


@pytest.mark.asyncio
async def test_postgres_outbox_claim_idempotency_and_transaction_rollback(
    postgres_sessions: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_job(
        postgres_sessions,
        event_id="evt-outbox",
        ticket_id=4105,
        job_id="job-outbox",
    )
    draft = OutboxDraft(
        outbox_id="out-1",
        job_id="job-outbox",
        ticket_id=4105,
        generation=1,
        snapshot_cursor="cursor-1",
        locale="en",
        body="answer",
        idempotency_key="reply:4105:1:cursor-1",
    )
    async with postgres_sessions() as session, session.begin():
        first = await OutboxRepository(session).create(draft)
        duplicate = await OutboxRepository(session).create(draft)
        assert first.outbox_id == duplicate.outbox_id

    async with postgres_sessions() as session, session.begin():
        claimed = await OutboxRepository(session).claim_pending(
            worker_id="delivery-worker", now=NOW
        )
        assert claimed is not None
        assert claimed.status == OutboxStatus.ATTEMPTED
        assert claimed.attempt_count == 1
        assert await OutboxRepository(session).mark_sent(
            outbox_id=claimed.outbox_id, provider_message_id="provider-1", now=NOW
        )
        assert await OutboxRepository(session).mark_confirmed(
            outbox_id=claimed.outbox_id, provider_message_id="provider-1", now=NOW
        )

    rolled_back = OutboxDraft(
        outbox_id="out-rollback",
        job_id="job-outbox",
        ticket_id=4105,
        generation=1,
        snapshot_cursor="cursor-rollback",
        locale="en",
        body="must rollback",
        idempotency_key="reply:4105:1:cursor-rollback",
    )
    with pytest.raises(RuntimeError, match="rollback outbox"):
        async with postgres_sessions() as session, session.begin():
            await OutboxRepository(session).create(rolled_back)
            raise RuntimeError("rollback outbox")
    async with postgres_sessions() as session:
        count = cast(
            int | None,
            await session.scalar(
                text(
                    "SELECT count(*) FROM outbox_messages "
                    "WHERE outbox_id = 'out-rollback'"
                )
            ),
        )
    assert count == 0
