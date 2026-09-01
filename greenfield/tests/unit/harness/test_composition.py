from __future__ import annotations

import asyncio
from contextlib import suppress

import pytest

from casework_harness.composition import HarnessReadiness, create_app


class _ReadyDatabase:
    async def check(self) -> bool:
        return True


class _ReadyDocker:
    def ping(self) -> bool:
        return True


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
