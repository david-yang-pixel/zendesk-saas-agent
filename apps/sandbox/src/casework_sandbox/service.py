"""HTTP readiness entrypoint for an ephemeral sandbox container.

The Harness controller creates a fresh container from this image for each
ticket attempt.  The initial runtime keeps its transport health endpoint
small; the versioned StartRun/RunResult contracts carry the actual work.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Protocol

from fastapi import FastAPI, HTTPException

from contracts.outcome import Outcome
from contracts.sandbox import (
    SandboxCancelRequest,
    SandboxResultMessage,
    SandboxStartRequest,
)


class SandboxCommandHandler(Protocol):
    async def run(self, request: SandboxStartRequest) -> Outcome: ...

    async def cancel(self, request: SandboxCancelRequest) -> None: ...


class UnconfiguredSandboxCommands:
    async def is_ready(self) -> bool:
        return False

    async def run(self, request: SandboxStartRequest) -> Outcome:
        raise HTTPException(status_code=503, detail="sandbox runtime is unconfigured")

    async def cancel(self, request: SandboxCancelRequest) -> None:
        raise HTTPException(status_code=503, detail="sandbox runtime is unconfigured")


async def healthz() -> dict[str, str]:
    return {"status": "ok"}


async def readyz() -> dict[str, str]:
    return {"status": "ready"}


_legacy_healthz = healthz
_legacy_readyz = readyz


def create_app(commands: SandboxCommandHandler | None = None) -> FastAPI:
    if commands is None:
        from casework_sandbox.composition import create_production_commands

        command_handler = create_production_commands()
    else:
        command_handler = commands

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        try:
            yield
        finally:
            close = getattr(command_handler, "aclose", None)
            if close is not None:
                await close()

    app = FastAPI(title="casework-sandbox", lifespan=lifespan)

    async def ready() -> dict[str, str]:
        try:
            readiness = getattr(command_handler, "is_ready", None)
            if readiness is None or await readiness():
                return {"status": "ready"}
        except Exception:
            pass
        raise HTTPException(status_code=503, detail="sandbox runtime is not ready")

    async def start_run(request: SandboxStartRequest) -> SandboxResultMessage:
        outcome = await command_handler.run(request)
        return SandboxResultMessage(
            sandbox_id=request.sandbox_id,
            session_id=request.session_id,
            outcome=outcome,
            telemetry=outcome.telemetry,
            finished_at=datetime.now(UTC),
        )

    async def cancel_run(request: SandboxCancelRequest) -> dict[str, str]:
        await command_handler.cancel(request)
        return {"status": "accepted"}

    app.add_api_route("/healthz", healthz, methods=["GET"])
    app.add_api_route("/readyz", ready, methods=["GET"])
    app.add_api_route(
        "/v1/runs",
        start_run,
        methods=["POST"],
        response_model=SandboxResultMessage,
    )
    app.add_api_route(
        "/v1/cancel", cancel_run, methods=["POST"], status_code=202
    )
    return app
