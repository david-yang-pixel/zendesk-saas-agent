from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import pytest

from casework_harness.postgres_queue import LeaseLostError, PostgresJobQueue
from casework_harness.worker import JobLease
from casework_persistence.models import CaseJobRow


class _Gateway:
    def __init__(self, *, lease_valid: bool = True) -> None:
        self.lease_valid = lease_valid
        self.claimed = False
        self.statuses: list[str] = []
        self.heartbeat_count = 0
        self.retries: list[tuple[int, str]] = []

    async def claim_next(
        self, *, worker_id: str, lease_seconds: int, max_attempts: int
    ) -> CaseJobRow | None:
        if self.claimed:
            return None
        self.claimed = True
        return CaseJobRow(
            job_id="job-1",
            session_id="session-1",
            event_id="evt-1",
            expected_event_id="messaging:evt-1",
            ticket_id=3726,
            generation=1,
            status="claimed",
            attempts=1,
            available_at=datetime(2026, 9, 1, tzinfo=UTC),
            config_revision="cfg-1",
            root_model_config_id="root",
            child_model_config_ids=["logs"],
            model_digest="sha256:model",
            skill_digest="sha256:skill",
            tool_digest="sha256:tool",
        )

    async def transition(
        self, *, job_id: str, worker_id: str, status: str
    ) -> bool:
        self.statuses.append(status)
        return self.lease_valid

    async def heartbeat(
        self, *, job_id: str, worker_id: str, lease_seconds: int
    ) -> bool:
        self.heartbeat_count += 1
        return self.lease_valid

    async def finish(
        self,
        *,
        job_id: str,
        worker_id: str,
        status: str,
        dead_letter_reason: str | None = None,
    ) -> bool:
        self.statuses.append(status)
        return self.lease_valid

    async def retry(
        self,
        *,
        job_id: str,
        worker_id: str,
        delay_seconds: int,
        reason: str,
    ) -> bool:
        self.retries.append((delay_seconds, reason))
        return self.lease_valid


@asynccontextmanager
async def _scope(gateway: _Gateway) -> AsyncGenerator[_Gateway, None]:
    yield gateway


@pytest.mark.asyncio
async def test_postgres_queue_maps_durable_job_and_owns_short_operations() -> None:
    gateway = _Gateway()
    queue = PostgresJobQueue(
        gateway_scope=lambda: _scope(gateway),
        worker_id="worker-1",
        lease_seconds=90,
    )

    lease = await queue.claim()
    assert lease is not None
    assert lease.session_id == "session-1"
    assert lease.event_id == "evt-1"
    assert lease.attempts == 1
    assert lease.generation == 1

    await queue.transition(lease, "running")
    await queue.heartbeat(lease)
    await queue.finish(lease, "completed")

    assert gateway.statuses == ["running", "completed"]
    assert gateway.heartbeat_count == 1


@pytest.mark.asyncio
async def test_postgres_queue_requeues_owned_lease_until_attempt_budget() -> None:
    gateway = _Gateway()
    queue = PostgresJobQueue(
        gateway_scope=lambda: _scope(gateway),
        worker_id="worker-1",
        max_attempts=2,
    )
    lease = await queue.claim()
    assert lease is not None

    assert await queue.retry(
        lease, delay_seconds=2, reason="snapshot_not_ready"
    )
    assert gateway.retries == [(2, "snapshot_not_ready")]

    exhausted = JobLease(
        job_id=lease.job_id,
        session_id=lease.session_id,
        event_id=lease.event_id,
        expected_event_id=lease.expected_event_id,
        ticket_id=lease.ticket_id,
        generation=lease.generation,
        attempts=2,
        config_revision=lease.config_revision,
        model_digest=lease.model_digest,
        skill_digest=lease.skill_digest,
        tool_digest=lease.tool_digest,
    )
    assert not await queue.retry(
        exhausted, delay_seconds=4, reason="snapshot_not_ready"
    )
    assert gateway.retries == [(2, "snapshot_not_ready")]


@pytest.mark.asyncio
async def test_postgres_queue_rejects_updates_after_lease_loss() -> None:
    gateway = _Gateway(lease_valid=False)
    queue = PostgresJobQueue(
        gateway_scope=lambda: _scope(gateway),
        worker_id="worker-1",
    )
    lease = await queue.claim()
    assert lease is not None

    with pytest.raises(LeaseLostError):
        await queue.transition(lease, "running")
