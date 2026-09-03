from __future__ import annotations

import asyncio
from contextlib import suppress

import pytest

from casework_harness.composition import (
    HarnessReadiness,
    SandboxEnvironmentError,
    create_app,
    sandbox_environment,
)


class _ReadyDatabase:
    async def check(self) -> bool:
        return True


class _ReadyDocker:
    def ping(self) -> bool:
        return True


class _ReadySandboxProbe:
    def __init__(self, result: bool = True) -> None:
        self.result = result
        self.calls = 0

    async def check(self) -> bool:
        self.calls += 1
        return self.result


def test_production_harness_factory_requires_database_and_sandbox_image(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("SANDBOX_IMAGE", raising=False)
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        create_app()

    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://localhost/casework")
    with pytest.raises(RuntimeError, match="SANDBOX_IMAGE"):
        create_app()


def test_sandbox_environment_is_explicit_and_never_contains_database_url() -> None:
    environment = sandbox_environment(
        {
            "DATABASE_URL": "postgresql://must-not-cross-boundary",
            "ZENDESK_BASE_URL": "https://example.zendesk.com",
            "ZENDESK_EMAIL": "agent@example.com",
            "ZENDESK_API_TOKEN": "token",
            "CASEWORK_MODEL_BASE_URL": "https://model.example.com",
            "CASEWORK_TOOL_CONFIG": "[]",
            "UNRELATED_SECRET": "must-not-cross-boundary",
        }
    )

    assert set(environment) == {
        "ZENDESK_BASE_URL",
        "ZENDESK_EMAIL",
        "ZENDESK_API_TOKEN",
        "CASEWORK_MODEL_BASE_URL",
        "CASEWORK_TOOL_CONFIG",
    }

    with pytest.raises(SandboxEnvironmentError, match="ZENDESK_API_TOKEN"):
        sandbox_environment({})


@pytest.mark.asyncio
async def test_live_without_sender_is_not_ready_but_dry_run_can_be_ready() -> None:
    stop = asyncio.Event()
    task = asyncio.create_task(stop.wait())
    try:
        live = HarnessReadiness(
            database=_ReadyDatabase(),
            docker_client=_ReadyDocker(),
            delivery_ready=False,
        )
        live.attach_tasks(task)
        assert await live.check() is False

        dry_run = HarnessReadiness(
            database=_ReadyDatabase(),
            docker_client=_ReadyDocker(),
            delivery_ready=True,
        )
        dry_run.attach_tasks(task)
        assert await dry_run.check() is True

        canary = HarnessReadiness(
            database=_ReadyDatabase(),
            docker_client=_ReadyDocker(),
            delivery_ready=False,
        )
        canary.attach_tasks(task)
        assert await canary.check() is False
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


@pytest.mark.asyncio
async def test_harness_readiness_requires_the_cached_sandbox_launch_probe() -> None:
    stop = asyncio.Event()
    task = asyncio.create_task(stop.wait())
    probe = _ReadySandboxProbe()
    try:
        readiness = HarnessReadiness(
            database=_ReadyDatabase(),
            docker_client=_ReadyDocker(),
            delivery_ready=True,
            sandbox_probe=probe,
        )
        readiness.attach_tasks(task)

        assert await readiness.check() is True
        assert probe.calls == 1

        probe.result = False
        assert await readiness.check() is False
        assert probe.calls == 2
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
