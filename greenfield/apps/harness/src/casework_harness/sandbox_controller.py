"""Controller-owned lifecycle for one ephemeral ticket sandbox per attempt."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Protocol

from casework_harness.sandbox_policy import SandboxFailure, SandboxResourcePolicy


class SandboxExecutionStatus(StrEnum):
    SUCCEEDED = "succeeded"
    RESOURCE_EXHAUSTED = "resource_exhausted"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class SandboxExecution:
    status: SandboxExecutionStatus
    result: dict[str, Any] | None = None
    error_code: str | None = None


@dataclass(frozen=True, slots=True)
class SandboxRunResult:
    sandbox_id: str
    attempt: int
    status: SandboxExecutionStatus
    result: dict[str, Any] | None = None
    error_code: str | None = None


class SandboxCleanupError(RuntimeError):
    """Raised when a managed sandbox cannot be confirmed destroyed."""


class SandboxResourceExhaustedError(RuntimeError):
    """Raised by an engine when a sandbox cannot continue due to its limits."""

    def __init__(self, error_code: str) -> None:
        super().__init__(error_code)
        self.error_code = error_code


class SandboxEngine(Protocol):
    async def create(
        self, *, image: str, limits: dict[str, int], labels: dict[str, str]
    ) -> str: ...

    async def wait_ready(self, sandbox_id: str, *, timeout_seconds: int) -> None: ...

    async def execute(
        self,
        sandbox_id: str,
        *,
        request: dict[str, Any],
        timeout_seconds: int,
    ) -> SandboxExecution: ...

    async def destroy(self, sandbox_id: str) -> None: ...


class SandboxController:
    """Run a sandbox, clean it up, and permit only the fixed resource retry."""

    def __init__(
        self,
        *,
        engine: SandboxEngine,
        image: str,
        policy: SandboxResourcePolicy | None = None,
    ) -> None:
        self._engine = engine
        self._image = image
        self._policy = policy or SandboxResourcePolicy()

    async def run(self, request: dict[str, Any]) -> SandboxRunResult:
        first = await self._run_attempt(
            request=request, attempt=1, previous_failure=None
        )
        if first.status is not SandboxExecutionStatus.RESOURCE_EXHAUSTED:
            return first
        return await self._run_attempt(
            request=request,
            attempt=2,
            previous_failure=SandboxFailure.RESOURCE_EXHAUSTED,
        )

    async def _run_attempt(
        self,
        *,
        request: dict[str, Any],
        attempt: int,
        previous_failure: SandboxFailure | None,
    ) -> SandboxRunResult:
        allocation = self._policy.allocation_for(
            attempt=attempt, previous_failure=previous_failure
        )
        sandbox_id = await self._engine.create(
            image=self._image,
            limits=allocation.docker_limits(),
            labels=self._labels(request=request, attempt=attempt),
        )
        execution: SandboxExecution
        try:
            try:
                await self._engine.wait_ready(
                    sandbox_id, timeout_seconds=allocation.timeout_seconds
                )
                execution_request = {
                    **request,
                    "sandbox_id": sandbox_id,
                    "resource_attempt": attempt,
                    "deadline": (
                        datetime.now(UTC)
                        + timedelta(seconds=allocation.timeout_seconds)
                    )
                    .replace(microsecond=0)
                    .isoformat()
                    .replace("+00:00", "Z"),
                    "resources": {
                        "cpu": allocation.cpus,
                        "memory_mb": allocation.memory_bytes // (1024**2),
                        "timeout_seconds": allocation.timeout_seconds,
                    },
                }
                execution = await self._engine.execute(
                    sandbox_id,
                    request=execution_request,
                    timeout_seconds=allocation.timeout_seconds,
                )
            except SandboxResourceExhaustedError as error:
                execution = SandboxExecution(
                    status=SandboxExecutionStatus.RESOURCE_EXHAUSTED,
                    error_code=error.error_code,
                )
        finally:
            try:
                await self._engine.destroy(sandbox_id)
            except Exception as error:
                raise SandboxCleanupError(
                    f"failed to destroy managed sandbox {sandbox_id}"
                ) from error

        return SandboxRunResult(
            sandbox_id=sandbox_id,
            attempt=attempt,
            status=execution.status,
            result=execution.result,
            error_code=execution.error_code,
        )

    @staticmethod
    def _labels(*, request: dict[str, Any], attempt: int) -> dict[str, str]:
        return {
            "casework.managed": "true",
            "casework.job_id": str(request["job_id"]),
            "casework.session_id": str(request["session_id"]),
            "casework.attempt": str(attempt),
        }
