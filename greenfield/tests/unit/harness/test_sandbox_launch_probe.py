from __future__ import annotations

import asyncio
from typing import Any, cast

import pytest

from casework_harness.docker_engine import (
    DockerClientPort,
    SandboxLaunchProbe,
    SandboxProbeResult,
)
from casework_harness.sandbox_controller import SandboxEngine


class _FakeImageCollection:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.get_calls: list[str] = []

    def get(self, image: str) -> object:
        self.get_calls.append(image)
        if self.error is not None:
            raise self.error
        return object()


class _FakeDockerClient:
    def __init__(
        self, *, ping_result: bool = True, image_error: Exception | None = None
    ) -> None:
        self.ping_result = ping_result
        self.ping_calls = 0
        self.images = _FakeImageCollection(error=image_error)

    def ping(self) -> bool:
        self.ping_calls += 1
        return self.ping_result


class _FakeEngine:
    def __init__(self, *, wait_error: BaseException | None = None) -> None:
        self.wait_error = wait_error
        self.create_calls: list[dict[str, Any]] = []
        self.wait_calls: list[tuple[str, int]] = []
        self.destroyed: list[str] = []
        self.execute_called = False

    async def create(
        self, *, image: str, limits: dict[str, int], labels: dict[str, str]
    ) -> str:
        self.create_calls.append(
            {"image": image, "limits": limits, "labels": labels}
        )
        return f"probe-sandbox-{len(self.create_calls)}"

    async def wait_ready(self, sandbox_id: str, *, timeout_seconds: int) -> None:
        self.wait_calls.append((sandbox_id, timeout_seconds))
        if self.wait_error is not None:
            raise self.wait_error

    async def execute(
        self,
        sandbox_id: str,
        *,
        request: dict[str, Any],
        timeout_seconds: int,
    ) -> object:
        del sandbox_id, request, timeout_seconds
        self.execute_called = True
        raise AssertionError("launch probe must never execute a run")

    async def destroy(self, sandbox_id: str) -> None:
        self.destroyed.append(sandbox_id)


def _client(client: _FakeDockerClient) -> DockerClientPort:
    return cast(DockerClientPort, client)


def _engine(engine: _FakeEngine) -> SandboxEngine:
    return cast(SandboxEngine, engine)


@pytest.mark.asyncio
async def test_probe_inspects_image_launches_readyz_and_always_destroys() -> None:
    client = _FakeDockerClient()
    engine = _FakeEngine()
    probe = SandboxLaunchProbe(
        client=_client(client),
        engine=_engine(engine),
        image="casework-sandbox@sha256:test",
        cache_ttl_seconds=60,
    )

    result = await probe.probe()

    assert result == SandboxProbeResult(ready=True, reason="ready", cached=False)
    assert client.ping_calls == 1
    assert client.images.get_calls == ["casework-sandbox@sha256:test"]
    assert len(engine.create_calls) == 1
    assert engine.create_calls[0]["limits"] == {
        "nano_cpus": 500_000_000,
        "mem_limit": 1024**3,
        "memswap_limit": 1024**3,
    }
    assert engine.create_calls[0]["labels"]["casework.probe"] == "true"
    assert "ticket_id" not in engine.create_calls[0]["labels"]
    assert engine.destroyed == ["probe-sandbox-1"]
    assert engine.execute_called is False

    cached = await probe.probe()

    assert cached == SandboxProbeResult(ready=True, reason="ready", cached=True)
    assert len(engine.create_calls) == 1
    assert client.ping_calls == 1


@pytest.mark.asyncio
async def test_probe_serializes_concurrent_checks_and_does_not_rebuild() -> None:
    client = _FakeDockerClient()
    engine = _FakeEngine()
    probe = SandboxLaunchProbe(
        client=_client(client),
        engine=_engine(engine),
        image="sandbox",
        cache_ttl_seconds=60,
    )

    results = await asyncio.gather(probe.probe(), probe.probe())

    assert [result.ready for result in results] == [True, True]
    assert len(engine.create_calls) == 1
    assert len(engine.destroyed) == 1
    assert sum(result.cached for result in results) == 1


@pytest.mark.asyncio
async def test_probe_destroy_runs_when_readiness_fails_and_failure_is_throttled(
) -> None:
    client = _FakeDockerClient()
    engine = _FakeEngine(wait_error=RuntimeError("not ready"))
    monotonic = 100.0
    probe = SandboxLaunchProbe(
        client=_client(client),
        engine=_engine(engine),
        image="sandbox",
        cache_ttl_seconds=60,
        failure_retry_seconds=10,
        monotonic_clock=lambda: monotonic,
    )

    first = await probe.probe()
    second = await probe.probe()

    assert first == SandboxProbeResult(
        ready=False, reason="readiness_failed", cached=False
    )
    assert second == SandboxProbeResult(
        ready=False, reason="readiness_failed", cached=True
    )
    assert engine.destroyed == ["probe-sandbox-1"]
    assert len(engine.create_calls) == 1


@pytest.mark.asyncio
async def test_probe_fails_closed_before_launch_when_docker_or_image_is_unavailable(
) -> None:
    unavailable = _FakeDockerClient(ping_result=False)
    unavailable_engine = _FakeEngine()
    unavailable_probe = SandboxLaunchProbe(
        client=_client(unavailable),
        engine=_engine(unavailable_engine),
        image="sandbox",
    )
    permission_result = await unavailable_probe.probe()

    missing = _FakeDockerClient(image_error=RuntimeError("image missing"))
    missing_engine = _FakeEngine()
    missing_probe = SandboxLaunchProbe(
        client=_client(missing),
        engine=_engine(missing_engine),
        image="sandbox",
    )
    image_result = await missing_probe.probe()

    assert permission_result.ready is False
    assert permission_result.reason == "docker_unavailable"
    assert image_result.ready is False
    assert image_result.reason == "sandbox_image_unavailable"
    assert unavailable_engine.create_calls == []
    assert missing_engine.create_calls == []


@pytest.mark.asyncio
async def test_probe_destroys_sandbox_when_probe_task_is_cancelled() -> None:
    client = _FakeDockerClient()
    engine = _FakeEngine(wait_error=asyncio.CancelledError())
    probe = SandboxLaunchProbe(
        client=_client(client),
        engine=_engine(engine),
        image="sandbox",
    )

    with pytest.raises(asyncio.CancelledError):
        await probe.probe()

    assert engine.destroyed == ["probe-sandbox-1"]


@pytest.mark.asyncio
async def test_probe_reports_docker_permission_denied_separately() -> None:
    client = _FakeDockerClient(image_error=PermissionError("forbidden"))
    engine = _FakeEngine()
    probe = SandboxLaunchProbe(
        client=_client(client),
        engine=_engine(engine),
        image="sandbox",
    )

    result = await probe.probe()

    assert result.ready is False
    assert result.reason == "docker_permission_denied"
    assert engine.create_calls == []
