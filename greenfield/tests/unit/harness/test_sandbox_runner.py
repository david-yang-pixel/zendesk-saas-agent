from __future__ import annotations

from casework_harness.sandbox_controller import (
    SandboxExecutionStatus,
    SandboxRunResult,
)
from casework_harness.sandbox_runner import ControllerSandboxRunner
from casework_harness.worker import SandboxRunRequest


class _Controller:
    async def run(self, request: dict[str, object]) -> SandboxRunResult:
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
        )


async def test_controller_runner_validates_typed_sandbox_result() -> None:
    runner = ControllerSandboxRunner(controller=_Controller())

    outcome = await runner.run(
        SandboxRunRequest(
            job_id="job-1",
            session_id="session-1",
            ticket_id=3726,
            generation=1,
            config_revision="cfg-1",
            model_digest="sha256:model",
            skill_digest="sha256:skill",
            tool_digest="sha256:tool",
        )
    )

    assert outcome.ticket_id == 3726
    assert outcome.answer == "Resolved with evidence."
