from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import pytest

from casework_harness.delivery import DeliveryDisposition, DeliveryResult
from casework_harness.worker import (
    HarnessWorker,
    JobLease,
    SandboxRunRequest,
)
from contracts.outcome import Outcome, OutcomeKind


@dataclass
class FakeJobs:
    lease: JobLease | None
    transitions: list[str] = field(default_factory=list[str])
    finished: list[str] = field(default_factory=list[str])
    heartbeats: int = 0
    heartbeat_event: asyncio.Event = field(default_factory=asyncio.Event)

    async def claim(self) -> JobLease | None:
        lease, self.lease = self.lease, None
        return lease

    async def transition(self, lease: JobLease, status: str) -> None:
        self.transitions.append(status)

    async def heartbeat(self, lease: JobLease) -> None:
        self.heartbeats += 1
        self.heartbeat_event.set()

    async def finish(self, lease: JobLease, status: str) -> None:
        self.finished.append(status)


@dataclass
class FakeSandboxRuns:
    outcome: Outcome
    requests: list[SandboxRunRequest] = field(default_factory=list[SandboxRunRequest])

    async def run(self, request: SandboxRunRequest) -> Outcome:
        self.requests.append(request)
        return self.outcome


@dataclass
class FakeDelivery:
    delivered: list[tuple[Outcome, int, str]] = field(
        default_factory=list[tuple[Outcome, int, str]]
    )

    async def deliver(
        self, outcome: Outcome, generation: int, config_revision: str
    ) -> None:
        self.delivered.append((outcome, generation, config_revision))


@dataclass
class FailedDelivery:
    async def deliver(
        self, outcome: Outcome, generation: int, config_revision: str
    ) -> DeliveryResult:
        del config_revision
        return DeliveryResult(
            disposition=DeliveryDisposition.FAILED,
            ticket_id=outcome.ticket_id,
            reason="live_sender_not_ready",
        )


@dataclass
class DisabledDelivery:
    async def deliver(
        self, outcome: Outcome, generation: int, config_revision: str
    ) -> DeliveryResult:
        del generation, config_revision
        return DeliveryResult(
            disposition=DeliveryDisposition.DISABLED,
            ticket_id=outcome.ticket_id,
            reason="reply_delivery_disabled",
        )


class RaisingDelivery:
    async def deliver(
        self, outcome: Outcome, generation: int, config_revision: str
    ) -> DeliveryResult:
        del outcome, generation, config_revision
        raise RuntimeError("delivery unavailable")


@dataclass
class FinalizingJobs(FakeJobs):
    finalized: list[tuple[Outcome, str]] = field(
        default_factory=list[tuple[Outcome, str]]
    )

    async def finalize(
        self,
        lease: JobLease,
        outcome: Outcome,
        status: str,
        delivery_result: DeliveryResult | None = None,
    ) -> None:
        del lease, delivery_result
        self.finalized.append((outcome, status))


def _lease() -> JobLease:
    return JobLease(
        job_id="job-1",
        session_id="session-1",
        ticket_id=3726,
        generation=7,
        config_revision="config-1",
        model_digest="model-sha",
        skill_digest="skill-sha",
        tool_digest="tool-sha",
    )


def _answer() -> Outcome:
    return Outcome(
        outcome_id="outcome-1",
        session_id="session-1",
        ticket_id=3726,
        kind=OutcomeKind.ANSWER,
        answer="Resolved with evidence.",
        reply_locale="en",
        snapshot_cursor="cursor-1",
        delivery_eligible=True,
        evidence_ids=("ev-1",),
    )


@pytest.mark.asyncio
async def test_worker_passes_ticket_id_to_sandbox_and_finishes_after_delivery() -> None:
    jobs = FakeJobs(_lease())
    sandboxes = FakeSandboxRuns(_answer())
    delivery = FakeDelivery()
    worker = HarnessWorker(jobs=jobs, sandboxes=sandboxes, delivery=delivery)

    processed = await worker.process_once()

    assert processed is True
    assert sandboxes.requests[0].business_input == {"ticket_id": 3726}
    assert sandboxes.requests[0].config_revision == "config-1"
    assert jobs.transitions == ["sandbox_creating", "running", "finalizing"]
    assert delivery.delivered == [(_answer(), 7, "config-1")]
    assert jobs.finished == ["completed"]


@pytest.mark.asyncio
async def test_worker_maps_ask_to_awaiting_customer() -> None:
    ask = Outcome(
        outcome_id="outcome-ask",
        session_id="session-1",
        ticket_id=3726,
        kind=OutcomeKind.ASK_CUSTOMER,
        question="Which run failed?",
        reply_locale="en",
        snapshot_cursor="cursor-1",
        delivery_eligible=True,
    )
    jobs = FakeJobs(_lease())
    worker = HarnessWorker(
        jobs=jobs,
        sandboxes=FakeSandboxRuns(ask),
        delivery=FakeDelivery(),
    )

    await worker.process_once()

    assert jobs.finished == ["awaiting_customer"]


@pytest.mark.asyncio
async def test_worker_finalizes_failed_delivery_as_failed_job() -> None:
    jobs = FakeJobs(_lease())
    worker = HarnessWorker(
        jobs=jobs,
        sandboxes=FakeSandboxRuns(_answer()),
        delivery=FailedDelivery(),
    )

    await worker.process_once()

    assert jobs.finished == ["failed"]


@pytest.mark.asyncio
async def test_worker_does_not_mark_disabled_delivery_as_failed() -> None:
    jobs = FakeJobs(_lease())
    worker = HarnessWorker(
        jobs=jobs,
        sandboxes=FakeSandboxRuns(_answer()),
        delivery=DisabledDelivery(),
    )

    await worker.process_once()

    assert jobs.finished == ["completed"]


@pytest.mark.asyncio
async def test_worker_uses_atomic_finalizer_when_queue_exposes_one() -> None:
    jobs = FinalizingJobs(_lease())
    worker = HarnessWorker(
        jobs=jobs,
        sandboxes=FakeSandboxRuns(_answer()),
        delivery=FakeDelivery(),
    )

    await worker.process_once()

    assert jobs.finalized == [(_answer(), "completed")]
    assert jobs.finished == []


@pytest.mark.asyncio
async def test_worker_persists_outcome_when_delivery_raises() -> None:
    jobs = FinalizingJobs(_lease())
    worker = HarnessWorker(
        jobs=jobs,
        sandboxes=FakeSandboxRuns(_answer()),
        delivery=RaisingDelivery(),
    )

    with pytest.raises(RuntimeError, match="delivery unavailable"):
        await worker.process_once()

    assert jobs.finalized == [(_answer(), "failed")]
    assert jobs.finished == []


@pytest.mark.asyncio
async def test_worker_returns_false_when_queue_is_empty() -> None:
    worker = HarnessWorker(
        jobs=FakeJobs(None),
        sandboxes=FakeSandboxRuns(_answer()),
        delivery=FakeDelivery(),
    )

    assert await worker.process_once() is False


@pytest.mark.asyncio
async def test_worker_heartbeats_while_the_sandbox_is_running() -> None:
    jobs = FakeJobs(_lease())

    class _WaitForHeartbeat:
        async def run(self, request: SandboxRunRequest) -> Outcome:
            await jobs.heartbeat_event.wait()
            return _answer()

    worker = HarnessWorker(
        jobs=jobs,
        sandboxes=_WaitForHeartbeat(),
        delivery=FakeDelivery(),
        heartbeat_interval_seconds=0.001,
    )

    await worker.process_once()

    assert jobs.heartbeats >= 1
