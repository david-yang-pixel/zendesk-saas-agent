"""Production composition root for the long-running Harness controller."""

from __future__ import annotations

import asyncio
import os
import socket
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Protocol, cast

import docker
import structlog
from fastapi import FastAPI

from casework_harness.docker_engine import (
    DockerClientPort,
    DockerSandboxEngine,
    DockerSandboxOrphanSweeper,
    HttpxSandboxHttp,
)
from casework_harness.postgres_queue import (
    PostgresJobQueue,
    postgres_job_gateway_scope,
)
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
from contracts.outcome import Outcome

_LOG = structlog.get_logger(__name__)


class _LifecycleDockerClient(DockerClientPort, Protocol):
    def ping(self) -> bool: ...

    def close(self) -> None: ...


class _DryRunDelivery:
    """Safe composition default: validate the run without external Zendesk writes."""

    async def deliver(self, outcome: Outcome, generation: int) -> None:
        await _LOG.ainfo(
            "delivery_dry_run",
            ticket_id=outcome.ticket_id,
            session_id=outcome.session_id,
            generation=generation,
        )


class _HarnessReadiness:
    def __init__(
        self,
        *,
        database: DatabaseReadiness,
        docker_client: _LifecycleDockerClient,
    ) -> None:
        self._database = database
        self._docker_client = docker_client
        self._tasks: tuple[asyncio.Task[None], ...] = ()

    def attach_tasks(self, *tasks: asyncio.Task[None]) -> None:
        self._tasks = tuple(tasks)

    async def check(self) -> bool:
        if not self._tasks or any(task.done() for task in self._tasks):
            return False
        if not await self._database.check():
            return False
        try:
            return bool(await asyncio.to_thread(self._docker_client.ping))
        except Exception:
            return False


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
    )
    controller = SandboxController(engine=sandbox_engine, image=sandbox_image)
    jobs = PostgresJobQueue(
        gateway_scope=postgres_job_gateway_scope(sessions),
        worker_id=controller_id,
    )
    worker = HarnessWorker(
        jobs=jobs,
        sandboxes=ControllerSandboxRunner(controller=controller),
        delivery=_DryRunDelivery(),
    )
    sweeper = DockerSandboxOrphanSweeper(
        client=docker_client,
        installation_id=installation_id,
        controller_id=controller_id,
    )
    readiness = _HarnessReadiness(
        database=database,
        docker_client=docker_client,
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
            await asyncio.to_thread(docker_client.close)
            await engine.dispose()

    return create_harness_app(readiness=readiness, lifespan=lifespan)


__all__ = ["create_app"]
