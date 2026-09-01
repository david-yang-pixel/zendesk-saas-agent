from __future__ import annotations

from typing import Any

import pytest

from casework_harness.sandbox_controller import (
    SandboxCleanupError,
    SandboxController,
    SandboxExecution,
    SandboxExecutionStatus,
)


class FakeSandboxEngine:
    def __init__(
        self,
        executions: list[SandboxExecution],
        *,
        destroy_failure: Exception | None = None,
    ) -> None:
        self.executions = executions
        self.destroy_failure = destroy_failure
        self.created: list[dict[str, Any]] = []
        self.ready: list[str] = []
        self.destroyed: list[str] = []

    async def create(
        self, *, image: str, limits: dict[str, int], labels: dict[str, str]
    ) -> str:
        sandbox_id = f"sandbox-{len(self.created) + 1}"
        self.created.append(
            {"id": sandbox_id, "image": image, "limits": limits, "labels": labels}
        )
        return sandbox_id

    async def wait_ready(self, sandbox_id: str, *, timeout_seconds: int) -> None:
        self.ready.append(sandbox_id)

    async def execute(
        self,
        sandbox_id: str,
        *,
        request: dict[str, Any],
        timeout_seconds: int,
    ) -> SandboxExecution:
        return self.executions.pop(0)

    async def destroy(self, sandbox_id: str) -> None:
        self.destroyed.append(sandbox_id)
        if self.destroy_failure is not None:
            raise self.destroy_failure


def start_request() -> dict[str, Any]:
    return {
        "job_id": "job-1",
        "session_id": "session-1",
        "ticket_id": 3726,
        "generation": 7,
        "config_revision": "config-1",
    }


@pytest.mark.asyncio
async def test_success_uses_default_limits_and_always_destroys() -> None:
    engine = FakeSandboxEngine(
        [SandboxExecution(status=SandboxExecutionStatus.SUCCEEDED, result={"ok": True})]
    )
    controller = SandboxController(engine=engine, image="casework-sandbox:sha")

    result = await controller.run(start_request())

    assert result.result == {"ok": True}
    assert result.attempt == 1
    assert engine.created[0]["limits"] == {
        "nano_cpus": 500_000_000,
        "mem_limit": 1024**3,
        "memswap_limit": 1024**3,
    }
    assert engine.created[0]["labels"] == {
        "casework.managed": "true",
        "casework.job_id": "job-1",
        "casework.session_id": "session-1",
        "casework.attempt": "1",
    }
    assert engine.destroyed == ["sandbox-1"]


@pytest.mark.asyncio
async def test_resource_exhaustion_retries_once_in_a_new_sandbox() -> None:
    engine = FakeSandboxEngine(
        [
            SandboxExecution(status=SandboxExecutionStatus.RESOURCE_EXHAUSTED),
            SandboxExecution(
                status=SandboxExecutionStatus.SUCCEEDED,
                result={"outcome": "answer"},
            ),
        ]
    )
    controller = SandboxController(engine=engine, image="casework-sandbox:sha")

    result = await controller.run(start_request())

    assert result.attempt == 2
    assert [item["id"] for item in engine.created] == ["sandbox-1", "sandbox-2"]
    assert engine.created[1]["limits"]["nano_cpus"] == 1_000_000_000
    assert engine.created[1]["limits"]["mem_limit"] == 1024**3
    assert engine.destroyed == ["sandbox-1", "sandbox-2"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status",
    [
        SandboxExecutionStatus.FAILED,
        SandboxExecutionStatus.TIMED_OUT,
        SandboxExecutionStatus.CANCELLED,
    ],
)
async def test_non_resource_terminal_result_does_not_retry(
    status: SandboxExecutionStatus,
) -> None:
    engine = FakeSandboxEngine([SandboxExecution(status=status, error_code="terminal")])
    controller = SandboxController(engine=engine, image="casework-sandbox:sha")

    result = await controller.run(start_request())

    assert result.status is status
    assert result.attempt == 1
    assert len(engine.created) == 1
    assert engine.destroyed == ["sandbox-1"]


@pytest.mark.asyncio
async def test_execution_exception_still_destroys_sandbox() -> None:
    class CrashingEngine(FakeSandboxEngine):
        async def execute(
            self,
            sandbox_id: str,
            *,
            request: dict[str, Any],
            timeout_seconds: int,
        ) -> SandboxExecution:
            raise RuntimeError("worker crashed")

    engine = CrashingEngine([])
    controller = SandboxController(engine=engine, image="casework-sandbox:sha")

    with pytest.raises(RuntimeError, match="worker crashed"):
        await controller.run(start_request())

    assert engine.destroyed == ["sandbox-1"]


@pytest.mark.asyncio
async def test_cleanup_failure_is_never_reported_as_success() -> None:
    engine = FakeSandboxEngine(
        [
            SandboxExecution(
                status=SandboxExecutionStatus.SUCCEEDED, result={"ok": True}
            )
        ],
        destroy_failure=RuntimeError("engine unavailable"),
    )
    controller = SandboxController(engine=engine, image="casework-sandbox:sha")

    with pytest.raises(SandboxCleanupError, match="sandbox-1"):
        await controller.run(start_request())
