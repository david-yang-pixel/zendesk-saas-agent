from dataclasses import dataclass, field

import pytest

from casework_harness.delivery import DeliveryResult
from casework_harness.worker import (
    HarnessWorker,
    JobLease,
    RuntimeBootstrapLoader,
    SandboxRunRequest,
)
from contracts.outcome import Outcome, OutcomeKind
from contracts.runtime import ModelBinding, RuntimeBootstrap


def _bootstrap() -> RuntimeBootstrap:
    return RuntimeBootstrap(
        config_revision="cfg-1",
        root_model=ModelBinding(
            provider="openai", model="root", credential_ref="secret/root"
        ),
        enabled_skills=("core-casework",),
        enabled_tools=(),
        model_digest="model",
        skill_digest="skill",
        tool_digest="tool",
    )


def _lease() -> JobLease:
    return JobLease(
        job_id="job-1",
        session_id="session-1",
        ticket_id=3726,
        generation=1,
        config_revision="cfg-1",
        model_digest="model",
        skill_digest="skill",
        tool_digest="tool",
    )


@dataclass
class _Jobs:
    lease: JobLease | None = field(default_factory=_lease)

    async def claim(self) -> JobLease | None:
        lease, self.lease = self.lease, None
        return lease

    async def transition(self, lease: JobLease, status: str) -> None:
        del lease, status

    async def heartbeat(self, lease: JobLease) -> None:
        del lease

    async def finish(self, lease: JobLease, status: str) -> None:
        del lease, status


@dataclass
class _Sandbox:
    requests: list[SandboxRunRequest] = field(
        default_factory=lambda: list[SandboxRunRequest]()
    )

    async def run(self, request: SandboxRunRequest) -> Outcome:
        self.requests.append(request)
        return Outcome(
            outcome_id="outcome-1",
            session_id=request.session_id,
            ticket_id=request.ticket_id,
            kind=OutcomeKind.FAILED,
            reason="test",
        )


class _Delivery:
    async def deliver(self, outcome: Outcome, generation: int) -> DeliveryResult | None:
        del outcome, generation
        return None


class _Loader(RuntimeBootstrapLoader):
    def __init__(self) -> None:
        self.revisions: list[str] = []

    async def load(self, *, config_revision: str) -> RuntimeBootstrap:
        self.revisions.append(config_revision)
        return _bootstrap()


@pytest.mark.asyncio
async def test_worker_loads_bootstrap_by_immutable_revision_before_sandbox_run(
) -> None:
    loader = _Loader()
    sandbox = _Sandbox()
    worker = HarnessWorker(
        jobs=_Jobs(),
        sandboxes=sandbox,
        delivery=_Delivery(),
        bootstrap_loader=loader,
    )

    await worker.process_once()

    assert loader.revisions == ["cfg-1"]
    assert sandbox.requests[0].bootstrap == _bootstrap()
