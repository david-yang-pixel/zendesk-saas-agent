"""Minimal HTTP entrypoint for the controller image.

The controller is deliberately separate from the ephemeral sandbox runtime.
This entrypoint only exposes process health; the durable worker and adapters
are composed by the Harness application around the same package.
"""

from __future__ import annotations

from fastapi import FastAPI


async def healthz() -> dict[str, str]:
    return {"status": "ok"}


async def readyz() -> dict[str, str]:
    return {"status": "ready"}


def create_app() -> FastAPI:
    app = FastAPI(title="casework-harness")
    app.add_api_route("/healthz", healthz, methods=["GET"])
    app.add_api_route("/readyz", readyz, methods=["GET"])
    return app


app = create_app()
