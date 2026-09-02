from __future__ import annotations

from datetime import UTC, datetime

from casework_harness.sandbox_controller import (
    SandboxExecutionStatus,
    SandboxRunResult,
)
from casework_harness.sandbox_runner import ControllerSandboxRunner
from casework_harness.worker import SandboxRunRequest
from contracts.runtime import ModelBinding, RuntimeBootstrap
from contracts.telemetry import SandboxLifecycleEvent

BOOTSTRAP = RuntimeBootstrap(
    config_revision="cfg-1",
    root_model=ModelBinding(
        provider="openai", model="root", credential_ref="secret/root"
    ),
    model_digest="sha256:model",
    skill_digest="sha256:skill",
    tool_digest="sha256:tool",
)


class _Controller:
    def __init__(self) -> None:
        self.requests: list[dict[str, object]] = []

    async def run(self, request: dict[str, object]) -> SandboxRunResult:
        self.requests.append(request)
        return SandboxRunResult(
            sandbox_id="sandbox-1",
            attempt=1,
            status=SandboxExecutionStatus.SUCCEEDED,
            result={
                "sandbox_id": "sandbox-1",
                "session_id": "session-1",
                "outcome": {
                    "outcome_id": "outcome-1",
                    "session_id": "session-1",
                    "ticket_id": 3726,
                    "kind": "answer",
                    "answer": "Resolved with evidence.",
                    "delivery_eligible": False,
                    "evidence_ids": ["ev-1"],
                },
            },
            lifecycle=(
                SandboxLifecycleEvent(
                    state="destroyed",
                    occurred_at=datetime(2026, 9, 1, tzinfo=UTC),
                    sandbox_id="sandbox-1",
                    attempt=1,
                ),
            ),
        )


async def test_controller_runner_validates_typed_sandbox_result() -> None:
    controller = _Controller()
    runner = ControllerSandboxRunner(controller=controller)

    outcome = await runner.run(
        SandboxRunRequest(
            job_id="job-1",
            session_id="session-1",
            event_id="evt-1",
            expected_event_id="messaging:evt-1",
            ticket_id=3726,
            generation=1,
            attempts=1,
            config_revision="cfg-1",
            model_digest="sha256:model",
            skill_digest="sha256:skill",
            tool_digest="sha256:tool",
            bootstrap=BOOTSTRAP,
        )
    )

    assert outcome.ticket_id == 3726
    assert outcome.answer == "Resolved with evidence."
    assert outcome.telemetry is not None
    assert outcome.telemetry.sandbox_lifecycle[0].state == "destroyed"
    assert controller.requests[0]["event_id"] == "evt-1"
    assert controller.requests[0]["expected_event_id"] == "messaging:evt-1"
