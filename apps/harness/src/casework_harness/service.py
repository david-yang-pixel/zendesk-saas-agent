"""Minimal HTTP entrypoint for the controller image.

The controller is deliberately separate from the ephemeral sandbox runtime.
This entrypoint only exposes process health; the durable worker and adapters
are composed by the Harness application around the same package.
"""

from __future__ import annotations

from typing import Protocol

from fastapi import FastAPI, HTTPException
from starlette.types import Lifespan


class ReadinessPort(Protocol):
    async def check(self) -> bool: ...


async def healthz() -> dict[str, str]:
    return {"status": "ok"}


def create_app(
    *,
    readiness: ReadinessPort | None = None,
    lifespan: Lifespan[FastAPI] | None = None,
) -> FastAPI:
    app = FastAPI(title="casework-harness", lifespan=lifespan)

    async def readyz() -> dict[str, str]:
        if readiness is not None and not await readiness.check():
            raise HTTPException(status_code=503, detail="harness_not_ready")
        return {"status": "ready"}

    app.add_api_route("/healthz", healthz, methods=["GET"])
    app.add_api_route("/readyz", readyz, methods=["GET"])
    return app


app = create_app()
