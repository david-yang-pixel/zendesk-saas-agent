from __future__ import annotations

from dataclasses import dataclass, field

import pytest

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

    async def claim(self) -> JobLease | None:
        lease, self.lease = self.lease, None
        return lease

    async def transition(self, lease: JobLease, status: str) -> None:
        self.transitions.append(status)

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
    delivered: list[tuple[Outcome, int]] = field(
        default_factory=list[tuple[Outcome, int]]
    )

    async def deliver(self, outcome: Outcome, generation: int) -> None:
        self.delivered.append((outcome, generation))


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
    assert delivery.delivered == [(_answer(), 7)]
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
async def test_worker_returns_false_when_queue_is_empty() -> None:
    worker = HarnessWorker(
        jobs=FakeJobs(None),
        sandboxes=FakeSandboxRuns(_answer()),
        delivery=FakeDelivery(),
    )

    assert await worker.process_once() is False

