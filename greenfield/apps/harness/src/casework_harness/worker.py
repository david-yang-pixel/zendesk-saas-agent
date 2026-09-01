"""Durable job-to-sandbox orchestration for the ZendeskCasework Harness."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass
from typing import Protocol, cast

from casework_harness.delivery import DeliveryDisposition, DeliveryResult
from contracts.outcome import Outcome, OutcomeKind
from contracts.runtime import RuntimeBootstrap


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
    bootstrap: RuntimeBootstrap | None = None

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
    bootstrap: RuntimeBootstrap | None = None

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
            bootstrap=lease.bootstrap,
        )


class RuntimeBootstrapLoader(Protocol):
    """Read the immutable config revision outside the sandbox."""

    async def load(self, *, config_revision: str) -> RuntimeBootstrap: ...


class JobQueuePort(Protocol):
    async def claim(self) -> JobLease | None: ...

    async def transition(self, lease: JobLease, status: str) -> None: ...

    async def heartbeat(self, lease: JobLease) -> None: ...

    async def finish(self, lease: JobLease, status: str) -> None: ...


class SandboxRunPort(Protocol):
    async def run(self, request: SandboxRunRequest) -> Outcome: ...


class OutcomeDeliveryPort(Protocol):
    async def deliver(
        self, outcome: Outcome, generation: int, config_revision: str
    ) -> DeliveryResult | None: ...


class _TerminalFinalizer(Protocol):
    async def __call__(
        self,
        *,
        lease: JobLease,
        outcome: Outcome,
        status: str,
        delivery_result: DeliveryResult | None = None,
    ) -> None: ...


class HarnessWorker:
    """Process one leased ticket while keeping queue, sandbox and delivery split."""

    def __init__(
        self,
        *,
        jobs: JobQueuePort,
        sandboxes: SandboxRunPort,
        delivery: OutcomeDeliveryPort,
        heartbeat_interval_seconds: float = 20.0,
        bootstrap_loader: RuntimeBootstrapLoader | None = None,
    ) -> None:
        if heartbeat_interval_seconds <= 0:
            raise ValueError("heartbeat_interval_seconds must be positive")
        self._jobs = jobs
        self._sandboxes = sandboxes
        self._delivery = delivery
        self._heartbeat_interval_seconds = heartbeat_interval_seconds
        self._bootstrap_loader = bootstrap_loader

    async def process_once(self) -> bool:
        lease = await self._jobs.claim()
        if lease is None:
            return False
        outcome: Outcome | None = None
        try:
            await self._jobs.transition(lease, "sandbox_creating")
            await self._jobs.transition(lease, "running")
            outcome = await self._run_with_heartbeat(
                lease, await self._request_for(lease)
            )
            self._validate_outcome(lease, outcome)
            await self._jobs.transition(lease, "finalizing")
            if outcome.delivery_eligible:
                delivery_result = await self._delivery.deliver(
                    outcome, lease.generation, lease.config_revision
                )
                if delivery_result is not None:
                    terminal_status = self._delivery_terminal_status(
                        delivery_result, outcome.kind
                    )
                    await self._finish_terminal(
                        lease, outcome, terminal_status, delivery_result
                    )
                    return True
            await self._finish_terminal(
                lease, outcome, self._terminal_status(outcome.kind)
            )
        except Exception:
            if outcome is None:
                await self._jobs.finish(lease, "failed")
            else:
                try:
                    await self._finish_terminal(lease, outcome, "failed")
                except Exception:
                    # Preserve the original failure while still releasing a
                    # lease if the telemetry transaction itself is unavailable.
                    await self._jobs.finish(lease, "failed")
            raise
        return True

    async def _request_for(self, lease: JobLease) -> SandboxRunRequest:
        bootstrap = lease.bootstrap
        if self._bootstrap_loader is not None:
            bootstrap = await self._bootstrap_loader.load(
                config_revision=lease.config_revision
            )
            if bootstrap.config_revision != lease.config_revision:
                raise ValueError("runtime bootstrap revision does not match lease")
            if bootstrap.model_digest != lease.model_digest:
                raise ValueError("runtime bootstrap model digest does not match lease")
            if bootstrap.skill_digest != lease.skill_digest:
                raise ValueError("runtime bootstrap skill digest does not match lease")
            if bootstrap.tool_digest != lease.tool_digest:
                raise ValueError("runtime bootstrap tool digest does not match lease")
        if bootstrap is lease.bootstrap:
            return SandboxRunRequest.from_lease(lease)
        return SandboxRunRequest.from_lease(
            JobLease(
                job_id=lease.job_id,
                session_id=lease.session_id,
                ticket_id=lease.ticket_id,
                generation=lease.generation,
                config_revision=lease.config_revision,
                model_digest=lease.model_digest,
                skill_digest=lease.skill_digest,
                tool_digest=lease.tool_digest,
                bootstrap=bootstrap,
            )
        )

    async def _finish_terminal(
        self,
        lease: JobLease,
        outcome: Outcome,
        status: str,
        delivery_result: DeliveryResult | None = None,
    ) -> None:
        finalizer = getattr(self._jobs, "finalize", None)
        if finalizer is not None:
            await cast(_TerminalFinalizer, finalizer)(
                lease=lease,
                outcome=outcome,
                status=status,
                delivery_result=delivery_result,
            )
            return
        await self._jobs.finish(lease, status)

    async def _run_with_heartbeat(
        self, lease: JobLease, request: SandboxRunRequest
    ) -> Outcome:
        sandbox_task = asyncio.create_task(self._sandboxes.run(request))
        try:
            while True:
                done, _pending = await asyncio.wait(
                    {sandbox_task},
                    timeout=self._heartbeat_interval_seconds,
                )
                if sandbox_task in done:
                    return await sandbox_task
                await self._jobs.heartbeat(lease)
        except BaseException:
            sandbox_task.cancel()
            with suppress(asyncio.CancelledError):
                await sandbox_task
            raise

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

    @staticmethod
    def _delivery_terminal_status(
        result: DeliveryResult, outcome_kind: OutcomeKind
    ) -> str:
        if result.disposition in {
            DeliveryDisposition.SENT,
            DeliveryDisposition.DRY_RUN,
            DeliveryDisposition.DEDUPLICATED,
        }:
            return HarnessWorker._terminal_status(outcome_kind)
        if result.disposition is DeliveryDisposition.FENCED:
            return "human_takeover"
        if result.disposition is DeliveryDisposition.STALE:
            return "cancelled"
        if result.disposition is DeliveryDisposition.DISABLED:
            return HarnessWorker._terminal_status(outcome_kind)
        return "failed"
