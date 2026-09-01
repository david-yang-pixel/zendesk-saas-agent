from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

import pytest

from casework_harness.docker_engine import (
    DockerClientPort,
    DockerSandboxEngine,
    DockerSandboxOrphanSweeper,
    SandboxHttpResponse,
    SandboxReadinessResourceExhausted,
)
from casework_harness.sandbox_controller import SandboxExecutionStatus


class FakeContainer:
    def __init__(
        self,
        container_id: str,
        *,
        labels: dict[str, str],
        oom_killed: bool = False,
    ) -> None:
        self.id = container_id
        self.labels = labels
        self.attrs: dict[str, Any] = {
            "Config": {"Labels": labels},
            "NetworkSettings": {
                "Ports": {"8103/tcp": [{"HostIp": "127.0.0.1", "HostPort": "49152"}]}
            },
            "State": {"OOMKilled": oom_killed, "ExitCode": 0},
        }
        self.started = False
        self.removed: list[dict[str, Any]] = []

    def start(self) -> None:
        self.started = True

    def reload(self) -> None:
        return None

    def remove(self, *, force: bool = False) -> object:
        self.removed.append({"force": force})
        return None


class FakeContainers:
    def __init__(self) -> None:
        self.created: list[FakeContainer] = []
        self.create_calls: list[dict[str, Any]] = []
        self.by_id: dict[str, FakeContainer] = {}
        self.list_calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> FakeContainer:
        self.create_calls.append(kwargs)
        container = FakeContainer(
            f"container-{len(self.created) + 1}", labels=kwargs["labels"]
        )
        self.created.append(container)
        self.by_id[container.id] = container
        return container

    def get(self, container_id: str) -> FakeContainer:
        return self.by_id[container_id]

    def list(self, **kwargs: Any) -> list[FakeContainer]:
        self.list_calls.append(kwargs)
        return list(self.created)


class FakeDockerClient:
    def __init__(self) -> None:
        self.containers = FakeContainers()


class FakeHttp:
    def __init__(self, responses: list[SandboxHttpResponse]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []

    async def get(self, url: str, *, timeout_seconds: int) -> SandboxHttpResponse:
        del timeout_seconds
        self.calls.append(("GET", url, None))
        return self.responses.pop(0)

    async def post(
        self,
        url: str,
        *,
        json: dict[str, Any],
        timeout_seconds: int,
    ) -> SandboxHttpResponse:
        del timeout_seconds
        self.calls.append(("POST", url, json))
        return self.responses.pop(0)


def _limits() -> dict[str, int]:
    return {
        "nano_cpus": 500_000_000,
        "mem_limit": 1024**3,
        "memswap_limit": 1024**3,
    }


def _docker(client: FakeDockerClient) -> DockerClientPort:
    """Keep the fake's richer inspection helpers out of the production port."""
    return cast(DockerClientPort, client)


@pytest.mark.asyncio
async def test_create_starts_container_and_resolves_published_endpoint() -> None:
    client = FakeDockerClient()
    engine = DockerSandboxEngine(
        client=_docker(client),
        http=FakeHttp([]),
        installation_id="install-1",
        controller_id="controller-1",
        environment={"ZENDESK_BASE_URL": "https://example.zendesk.com"},
        clock=lambda: datetime(2026, 9, 1, 12, 0, tzinfo=UTC),
    )

    sandbox_id = await engine.create(
        image="casework-sandbox@sha256:abc",
        limits=_limits(),
        labels={
            "casework.managed": "true",
            "casework.job_id": "job-1",
            "casework.session_id": "session-1",
            "casework.attempt": "1",
        },
    )

    container = client.containers.created[0]
    assert sandbox_id == container.id
    assert container.started
    assert client.containers.created[0].labels == {
        "casework.managed": "true",
        "casework.job_id": "job-1",
        "casework.session_id": "session-1",
        "casework.attempt": "1",
        "casework.installation_id": "install-1",
        "casework.controller_id": "controller-1",
        "casework.run_id": "job-1",
        "casework.expires_at": "2026-09-01T12:30:00Z",
    }
    assert client.containers.created[0].attrs["NetworkSettings"]["Ports"] == {
        "8103/tcp": [{"HostIp": "127.0.0.1", "HostPort": "49152"}]
    }
    assert await engine.endpoint(sandbox_id) == "http://127.0.0.1:49152"
    assert client.containers.create_calls[0]["environment"] == {
        "ZENDESK_BASE_URL": "https://example.zendesk.com"
    }


def test_sandbox_environment_rejects_database_credentials() -> None:
    with pytest.raises(ValueError, match="database credentials"):
        DockerSandboxEngine(
            client=_docker(FakeDockerClient()),
            http=FakeHttp([]),
            installation_id="install-1",
            controller_id="controller-1",
            environment={"DATABASE_URL": "postgresql://forbidden"},
        )


@pytest.mark.asyncio
async def test_readiness_and_run_use_versioned_sandbox_protocol() -> None:
    client = FakeDockerClient()
    http = FakeHttp(
        [
            SandboxHttpResponse(status_code=503, body={"status": "starting"}),
            SandboxHttpResponse(status_code=200, body={"status": "ready"}),
            SandboxHttpResponse(
                status_code=200,
                body={"sandbox_id": "container-1", "outcome": {"kind": "answer"}},
            ),
        ]
    )
    engine = DockerSandboxEngine(
        client=_docker(client),
        http=http,
        installation_id="install-1",
        controller_id="controller-1",
        poll_interval_seconds=0,
    )
    sandbox_id = await engine.create(
        image="sandbox",
        limits=_limits(),
        labels={"casework.job_id": "job-1"},
    )

    await engine.wait_ready(sandbox_id, timeout_seconds=1)
    execution = await engine.execute(
        sandbox_id,
        request={"ticket_id": 3726},
        timeout_seconds=30,
    )

    assert execution.status is SandboxExecutionStatus.SUCCEEDED
    assert execution.result == {
        "sandbox_id": "container-1",
        "outcome": {"kind": "answer"},
    }
    assert [call[:2] for call in http.calls] == [
        ("GET", "http://127.0.0.1:49152/readyz"),
        ("GET", "http://127.0.0.1:49152/readyz"),
        ("POST", "http://127.0.0.1:49152/v1/runs"),
    ]


@pytest.mark.asyncio
async def test_oom_state_is_reported_as_resource_exhausted() -> None:
    client = FakeDockerClient()
    http = FakeHttp(
        [SandboxHttpResponse(status_code=500, body={"detail": "container stopped"})]
    )
    engine = DockerSandboxEngine(
        client=_docker(client),
        http=http,
        installation_id="install-1",
        controller_id="controller-1",
    )
    sandbox_id = await engine.create(
        image="sandbox",
        limits=_limits(),
        labels={"casework.job_id": "job-1"},
    )
    client.containers.by_id[sandbox_id].attrs["State"] = {
        "OOMKilled": True,
        "ExitCode": 137,
    }

    execution = await engine.execute(
        sandbox_id,
        request={"ticket_id": 3726},
        timeout_seconds=30,
    )

    assert execution.status is SandboxExecutionStatus.RESOURCE_EXHAUSTED
    assert execution.error_code == "container_oom_killed"


@pytest.mark.asyncio
async def test_readiness_oom_raises_typed_resource_error_for_controller_retry() -> None:
    client = FakeDockerClient()
    http = FakeHttp(
        [SandboxHttpResponse(status_code=503, body={"status": "starting"})]
    )
    engine = DockerSandboxEngine(
        client=_docker(client),
        http=http,
        installation_id="install-1",
        controller_id="controller-1",
        poll_interval_seconds=0,
    )
    sandbox_id = await engine.create(
        image="sandbox",
        limits=_limits(),
        labels={"casework.job_id": "job-1"},
    )
    client.containers.by_id[sandbox_id].attrs["State"] = {
        "OOMKilled": True,
        "ExitCode": 137,
    }

    with pytest.raises(SandboxReadinessResourceExhausted):
        await engine.wait_ready(sandbox_id, timeout_seconds=0)


@pytest.mark.asyncio
async def test_destroy_forces_container_removal() -> None:
    client = FakeDockerClient()
    engine = DockerSandboxEngine(
        client=_docker(client),
        http=FakeHttp([]),
        installation_id="install-1",
        controller_id="controller-1",
    )
    sandbox_id = await engine.create(
        image="sandbox",
        limits=_limits(),
        labels={"casework.job_id": "job-1"},
    )

    await engine.destroy(sandbox_id)

    assert client.containers.by_id[sandbox_id].removed == [{"force": True}]


@pytest.mark.asyncio
async def test_orphan_sweeper_removes_only_this_controller_expired_sandboxes() -> None:
    client = FakeDockerClient()
    now = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
    own_expired = FakeContainer(
        "own-expired",
        labels={
            "casework.managed": "true",
            "casework.installation_id": "install-1",
            "casework.controller_id": "controller-1",
            "casework.expires_at": "2026-09-01T11:59:59Z",
        },
    )
    own_active = FakeContainer(
        "own-active",
        labels={
            "casework.managed": "true",
            "casework.installation_id": "install-1",
            "casework.controller_id": "controller-1",
            "casework.expires_at": "2026-09-01T12:00:01Z",
        },
    )
    other_controller = FakeContainer(
        "other-controller",
        labels={
            "casework.managed": "true",
            "casework.installation_id": "install-1",
            "casework.controller_id": "controller-2",
            "casework.expires_at": "2026-09-01T11:00:00Z",
        },
    )
    other_installation = FakeContainer(
        "other-installation",
        labels={
            "casework.managed": "true",
            "casework.installation_id": "install-2",
            "casework.controller_id": "controller-1",
            "casework.expires_at": "2026-09-01T11:00:00Z",
        },
    )
    invalid_expiry = FakeContainer(
        "invalid-expiry",
        labels={
            "casework.managed": "true",
            "casework.installation_id": "install-1",
            "casework.controller_id": "controller-1",
            "casework.expires_at": "not-a-date",
        },
    )
    client.containers.created = [
        own_expired,
        own_active,
        other_controller,
        other_installation,
        invalid_expiry,
    ]

    report = await DockerSandboxOrphanSweeper(
        client=_docker(client),
        installation_id="install-1",
        controller_id="controller-1",
        clock=lambda: now,
    ).sweep()

    assert report.scanned == 5
    assert report.destroyed == ("own-expired",)
    assert own_expired.removed == [{"force": True}]
    assert own_active.removed == []
    assert other_controller.removed == []
    assert other_installation.removed == []
    assert invalid_expiry.removed == []
    assert client.containers.list_calls == [
        {
            "all": True,
            "filters": {
                "label": [
                    "casework.managed=true",
                    "casework.installation_id=install-1",
                    "casework.controller_id=controller-1",
                ]
            },
        }
    ]
