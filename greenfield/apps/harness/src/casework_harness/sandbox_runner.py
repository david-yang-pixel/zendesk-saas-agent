"""Typed bridge from Harness job requests to the Docker sandbox controller."""

from __future__ import annotations

from typing import Any, Protocol

from casework_harness.sandbox_controller import (
    SandboxExecutionStatus,
    SandboxRunResult,
)
from casework_harness.worker import SandboxRunRequest
from contracts.outcome import Outcome
from contracts.sandbox import SandboxResultMessage
from contracts.telemetry import SandboxResultTelemetry


class SandboxRunFailed(RuntimeError):
    """The sandbox did not return a successful typed terminal result."""


class SandboxControllerPort(Protocol):
    async def run(self, request: dict[str, Any]) -> SandboxRunResult: ...


class ControllerSandboxRunner:
    def __init__(self, *, controller: SandboxControllerPort) -> None:
        self._controller = controller

    async def run(self, request: SandboxRunRequest) -> Outcome:
        if request.bootstrap is None:
            raise SandboxRunFailed("runtime bootstrap is missing")
        controller_request: dict[str, Any] = {
            "job_id": request.job_id,
            "session_id": request.session_id,
            "ticket_id": request.ticket_id,
            "generation": request.generation,
            "config_revision": request.config_revision,
            "model_digest": request.model_digest,
            "skill_digest": request.skill_digest,
            "tool_digest": request.tool_digest,
        }
        controller_request["bootstrap"] = request.bootstrap.model_dump(mode="json")
        result = await self._controller.run(controller_request)
        if (
            result.status is not SandboxExecutionStatus.SUCCEEDED
            or result.result is None
        ):
            raise SandboxRunFailed(result.error_code or result.status.value)
        message = SandboxResultMessage.model_validate(result.result)
        if message.sandbox_id != result.sandbox_id:
            raise SandboxRunFailed("sandbox result identity mismatch")
        if message.session_id != request.session_id:
            raise SandboxRunFailed("sandbox result session mismatch")
        outcome = message.outcome
        message_telemetry = message.telemetry or outcome.telemetry
        if result.lifecycle:
            telemetry = message_telemetry or SandboxResultTelemetry()
            outcome = outcome.model_copy(
                update={
                    "telemetry": telemetry.model_copy(
                        update={"sandbox_lifecycle": result.lifecycle}
                    )
                }
            )
        elif message_telemetry is not outcome.telemetry:
            outcome = outcome.model_copy(update={"telemetry": message_telemetry})
        return outcome


__all__ = ["ControllerSandboxRunner", "SandboxRunFailed"]
