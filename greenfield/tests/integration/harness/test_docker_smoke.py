from __future__ import annotations

import asyncio
import os
from typing import cast

import pytest

from casework_harness.docker_engine import (
    DockerClientPort,
    DockerSandboxEngine,
    HttpxSandboxHttp,
    SandboxLaunchProbe,
)


@pytest.mark.skipif(
    os.environ.get("CASEWORK_DOCKER_SMOKE") != "1",
    reason="set CASEWORK_DOCKER_SMOKE=1 to run against a Docker daemon",
)
@pytest.mark.asyncio
async def test_real_docker_sandbox_launch_probe_is_no_write() -> None:
    image = os.environ.get("SANDBOX_IMAGE", "").strip()
    if not image:
        pytest.skip("SANDBOX_IMAGE is required for the Docker smoke test")

    docker = pytest.importorskip("docker")
    client = docker.from_env()
    try:
        try:
            await asyncio.to_thread(client.ping)
        except Exception as error:
            pytest.skip(f"Docker daemon is unavailable: {error}")
        http = HttpxSandboxHttp()
        try:
            engine = DockerSandboxEngine(
                client=cast(DockerClientPort, client),
                http=http,
                installation_id="casework-smoke",
                controller_id="casework-smoke-controller",
                host="127.0.0.1",
            )
            result = await SandboxLaunchProbe(
                client=cast(DockerClientPort, client),
                engine=engine,
                image=image,
                cache_ttl_seconds=1,
            ).probe()
            assert result.ready, result.reason
        finally:
            await http.aclose()
    finally:
        await asyncio.to_thread(client.close)
