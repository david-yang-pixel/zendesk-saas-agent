"""Production composition root for the long-running Harness controller."""

from __future__ import annotations

import asyncio
import os
import socket
from collections.abc import AsyncGenerator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from typing import Any, Protocol, cast

import docker
import structlog
from fastapi import FastAPI

from casework_harness.delivery import (
    ControllerDeliveryAdapter,
    DeliveryController,
    DeliveryMode,
    DeliveryPolicy,
    PostgresDeliveryAdapter,
    PostgresDeliveryConfigLoader,
    create_zendesk_reply_sender,
    postgres_delivery_transaction_scope,
    resolve_delivery_mode,
)
from casework_harness.docker_engine import (
    DockerClientPort,
    DockerSandboxEngine,
    DockerSandboxOrphanSweeper,
    HttpxSandboxHttp,
    SandboxLaunchProbe,
)
from casework_harness.postgres_queue import (
    PostgresJobQueue,
    postgres_job_gateway_scope,
)
from casework_harness.runtime_bootstrap import PostgresRuntimeBootstrapLoader
from casework_harness.sandbox_controller import SandboxController
from casework_harness.sandbox_runner import ControllerSandboxRunner
from casework_harness.service import create_app as create_harness_app
from casework_harness.worker import HarnessWorker
from casework_persistence import (
    SCHEMA_HEAD,
    DatabaseReadiness,
    create_engine,
    create_session_factory,
)

_LOG = structlog.get_logger(__name__)
_SANDBOX_ENV_KEYS = (
    "ZENDESK_BASE_URL",
    "ZENDESK_EMAIL",
    "ZENDESK_API_TOKEN",
    "CASEWORK_MODEL_BASE_URL",
    "CASEWORK_TOOL_CONFIG",
)


class SandboxEnvironmentError(RuntimeError):
    """Required Sandbox adapter configuration is incomplete."""


def sandbox_environment(environ: Mapping[str, str]) -> dict[str, str]:
    environment = {
        key: environ.get(key, "").strip() for key in _SANDBOX_ENV_KEYS
    }
    missing = [key for key, value in environment.items() if not value]
    if missing:
        raise SandboxEnvironmentError(
            "missing sandbox environment: " + ", ".join(missing)
        )
    # Credential values are passed only through the explicitly namespaced
    # secret bridge.  They are never part of the runtime bootstrap or queue.
    environment.update(
        {
            key: value.strip()
            for key, value in environ.items()
            if key.startswith("CASEWORK_CREDENTIAL_") and value.strip()
        }
    )
    return environment


class _LifecycleDockerClient(DockerClientPort, Protocol):
    def ping(self) -> bool: ...

    def close(self) -> None: ...


class _DatabaseReadinessPort(Protocol):
    async def check(self) -> bool: ...


class _DockerReadinessPort(Protocol):
    def ping(self) -> bool: ...


class _SandboxLaunchProbePort(Protocol):
    async def check(self) -> bool: ...


class _HarnessReadiness:
    def __init__(
        self,
        *,
        database: _DatabaseReadinessPort,
        docker_client: _DockerReadinessPort,
        delivery_ready: bool,
        sandbox_probe: _SandboxLaunchProbePort | None = None,
    ) -> None:
        self._database = database
        self._docker_client = docker_client
        self._delivery_ready = delivery_ready
        self._sandbox_probe = sandbox_probe
        self._tasks: tuple[asyncio.Task[None], ...] = ()

    def attach_tasks(self, *tasks: asyncio.Task[Any]) -> None:
        self._tasks = tuple(tasks)

    async def check(self) -> bool:
        if not self._tasks or any(task.done() for task in self._tasks):
            return False
        if not self._delivery_ready:
            return False
        if not await self._database.check():
            return False
        try:
            if not await asyncio.to_thread(self._docker_client.ping):
                return False
        except Exception:
            return False
        if self._sandbox_probe is not None and not await self._sandbox_probe.check():
            return False
        return True


async def _wait_or_stop(stop: asyncio.Event, seconds: float) -> None:
    try:
        await asyncio.wait_for(stop.wait(), timeout=seconds)
    except TimeoutError:
        pass


async def _worker_loop(stop: asyncio.Event, worker: HarnessWorker) -> None:
    while not stop.is_set():
        try:
            processed = await worker.process_once()
        except asyncio.CancelledError:
            raise
        except Exception as error:
            await _LOG.aexception("harness_job_failed", error=str(error))
            await _wait_or_stop(stop, 2.0)
        else:
            if not processed:
                await _wait_or_stop(stop, 1.0)


async def _sweeper_loop(
    stop: asyncio.Event,
    sweep: Callable[[], Awaitable[object]],
) -> None:
    while not stop.is_set():
        try:
            await sweep()
        except asyncio.CancelledError:
            raise
        except Exception as error:
            await _LOG.aexception("sandbox_sweep_failed", error=str(error))
        await _wait_or_stop(stop, 60.0)


def create_app() -> FastAPI:
    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError("DATABASE_URL is required for production Harness")
    sandbox_image = os.environ.get("SANDBOX_IMAGE", "").strip()
    if not sandbox_image:
        raise RuntimeError("SANDBOX_IMAGE is required for production Harness")
    runtime_environment = sandbox_environment(os.environ)
    try:
        delivery_mode = resolve_delivery_mode(os.environ)
    except ValueError as error:
        raise RuntimeError(str(error)) from error
    canary_ticket_ids = _canary_ticket_ids(
        os.environ.get("CASEWORK_CANARY_TICKET_IDS")
    )

    installation_id = os.environ.get(
        "CASEWORK_INSTALLATION_ID", "casework-greenfield"
    ).strip()
    controller_id = os.environ.get(
        "CASEWORK_CONTROLLER_ID", socket.gethostname()
    ).strip()
    if not installation_id or not controller_id:
        raise RuntimeError("Casework installation and controller IDs are required")

    engine = create_engine(database_url)
    sessions = create_session_factory(engine)
    database = DatabaseReadiness(sessions, expected_revision=SCHEMA_HEAD)
    docker_client = cast(_LifecycleDockerClient, docker.from_env())
    sandbox_http = HttpxSandboxHttp()
    sandbox_engine = DockerSandboxEngine(
        client=docker_client,
        http=sandbox_http,
        installation_id=installation_id,
        controller_id=controller_id,
        host=os.environ.get("SANDBOX_HOST", "host.docker.internal"),
        environment=runtime_environment,
    )
    sandbox_probe = SandboxLaunchProbe(
        client=docker_client,
        engine=sandbox_engine,
        image=sandbox_image,
    )
    controller = SandboxController(engine=sandbox_engine, image=sandbox_image)
    jobs = PostgresJobQueue(
        gateway_scope=postgres_job_gateway_scope(sessions),
        worker_id=controller_id,
    )
    if delivery_mode is DeliveryMode.DRY_RUN:
        zendesk_sender, close_zendesk_sender = create_zendesk_reply_sender({})
    else:
        zendesk_sender, close_zendesk_sender = create_zendesk_reply_sender(
            os.environ
        )
    delivery_adapter = PostgresDeliveryAdapter(
        transaction_scope=postgres_delivery_transaction_scope(sessions)
    )
    delivery_config_loader = PostgresDeliveryConfigLoader(
        transaction_scope=postgres_delivery_transaction_scope(sessions)
    )
    delivery_controller = DeliveryController(
        freshness=delivery_adapter,
        outbox=delivery_adapter,
        sender=zendesk_sender,
        config_loader=delivery_config_loader,
        policy=DeliveryPolicy(
            mode=delivery_mode,
            canary_ticket_ids=frozenset(canary_ticket_ids),
        ),
    )
    worker = HarnessWorker(
        jobs=jobs,
        sandboxes=ControllerSandboxRunner(controller=controller),
        delivery=ControllerDeliveryAdapter(delivery_controller),
        bootstrap_loader=PostgresRuntimeBootstrapLoader(sessions),
    )
    sweeper = DockerSandboxOrphanSweeper(
        client=docker_client,
        installation_id=installation_id,
        controller_id=controller_id,
    )
    readiness = _HarnessReadiness(
        database=database,
        docker_client=docker_client,
        sandbox_probe=sandbox_probe,
        # Dry-run is always safe.  Live/canary require complete credentials;
        # the sender factory returns an unavailable sender otherwise.
        delivery_ready=(
            delivery_mode is DeliveryMode.DRY_RUN
            or getattr(zendesk_sender, "ready", False) is True
        ),
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
        stop = asyncio.Event()
        worker_task = asyncio.create_task(_worker_loop(stop, worker))
        sweeper_task = asyncio.create_task(_sweeper_loop(stop, sweeper.sweep))
        readiness.attach_tasks(worker_task, sweeper_task)
        try:
            yield
        finally:
            stop.set()
            worker_task.cancel()
            sweeper_task.cancel()
            await asyncio.gather(worker_task, sweeper_task, return_exceptions=True)
            await sandbox_http.aclose()
            await close_zendesk_sender()
            await asyncio.to_thread(docker_client.close)
            await engine.dispose()

    return create_harness_app(readiness=readiness, lifespan=lifespan)


def _canary_ticket_ids(value: str | None) -> tuple[int, ...]:
    if not value or not value.strip():
        return ()
    try:
        ticket_ids = tuple(int(item.strip()) for item in value.split(","))
    except ValueError as error:
        raise RuntimeError(
            "CASEWORK_CANARY_TICKET_IDS must be comma-separated integers"
        ) from error
    if any(ticket_id <= 0 for ticket_id in ticket_ids):
        raise RuntimeError("CASEWORK_CANARY_TICKET_IDS must be positive")
    return ticket_ids


HarnessReadiness = _HarnessReadiness


__all__ = [
    "HarnessReadiness",
    "SandboxEnvironmentError",
    "create_app",
    "sandbox_environment",
]
