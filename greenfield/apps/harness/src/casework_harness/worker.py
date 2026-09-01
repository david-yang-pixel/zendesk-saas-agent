"""Durable job-to-sandbox orchestration for the ZendeskCasework Harness."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from contracts.outcome import Outcome, OutcomeKind


@dataclass(frozen=True, slots=True)
class JobLease:
    job_id: str
    session_id: str
    ticket_id: int
    generation: int
    config_revision: str
    model_digest: str
    skill_digest: str
    tool_digest: str

    def __post_init__(self) -> None:
        if self.ticket_id <= 0 or self.generation < 1:
            raise ValueError("ticket_id and generation must be positive")
        required = (
            self.job_id,
            self.session_id,
            self.config_revision,
            self.model_digest,
            self.skill_digest,
            self.tool_digest,
        )
        if any(not value.strip() for value in required):
            raise ValueError("job lease requires frozen configuration metadata")


@dataclass(frozen=True, slots=True)
class SandboxRunRequest:
    job_id: str
    session_id: str
    ticket_id: int
    generation: int
    config_revision: str
    model_digest: str
    skill_digest: str
    tool_digest: str

    @property
    def business_input(self) -> dict[str, int]:
        return {"ticket_id": self.ticket_id}

    @classmethod
    def from_lease(cls, lease: JobLease) -> SandboxRunRequest:
        return cls(
            job_id=lease.job_id,
            session_id=lease.session_id,
            ticket_id=lease.ticket_id,
            generation=lease.generation,
            config_revision=lease.config_revision,
            model_digest=lease.model_digest,
            skill_digest=lease.skill_digest,
            tool_digest=lease.tool_digest,
        )


class JobQueuePort(Protocol):
    async def claim(self) -> JobLease | None: ...

    async def transition(self, lease: JobLease, status: str) -> None: ...

    async def finish(self, lease: JobLease, status: str) -> None: ...


class SandboxRunPort(Protocol):
    async def run(self, request: SandboxRunRequest) -> Outcome: ...


class OutcomeDeliveryPort(Protocol):
    async def deliver(self, outcome: Outcome, generation: int) -> None: ...


class HarnessWorker:
    """Process one leased ticket while keeping queue, sandbox and delivery split."""

    def __init__(
        self,
        *,
        jobs: JobQueuePort,
        sandboxes: SandboxRunPort,
        delivery: OutcomeDeliveryPort,
    ) -> None:
        self._jobs = jobs
        self._sandboxes = sandboxes
        self._delivery = delivery

    async def process_once(self) -> bool:
        lease = await self._jobs.claim()
        if lease is None:
            return False
        try:
            await self._jobs.transition(lease, "sandbox_creating")
            await self._jobs.transition(lease, "running")
            outcome = await self._sandboxes.run(SandboxRunRequest.from_lease(lease))
            self._validate_outcome(lease, outcome)
            await self._jobs.transition(lease, "finalizing")
            if outcome.delivery_eligible:
                await self._delivery.deliver(outcome, lease.generation)
            await self._jobs.finish(lease, self._terminal_status(outcome.kind))
        except Exception:
            await self._jobs.finish(lease, "failed")
            raise
        return True

    @staticmethod
    def _validate_outcome(lease: JobLease, outcome: Outcome) -> None:
        if outcome.ticket_id != lease.ticket_id:
            raise ValueError("sandbox outcome ticket does not match job lease")
        if outcome.session_id != lease.session_id:
            raise ValueError("sandbox outcome session does not match job lease")

    @staticmethod
    def _terminal_status(kind: OutcomeKind) -> str:
        return {
            OutcomeKind.ANSWER: "completed",
            OutcomeKind.ASK_CUSTOMER: "awaiting_customer",
            OutcomeKind.HUMAN_TAKEOVER: "human_takeover",
            OutcomeKind.FAILED: "failed",
            OutcomeKind.CANCELLED: "cancelled",
        }[kind]
