"""HTTP readiness entrypoint for an ephemeral sandbox container.

The Harness controller creates a fresh container from this image for each
ticket attempt.  The initial runtime keeps its transport health endpoint
small; the versioned StartRun/RunResult contracts carry the actual work.
"""

from __future__ import annotations

from fastapi import FastAPI


async def healthz() -> dict[str, str]:
    return {"status": "ok"}


async def readyz() -> dict[str, str]:
    return {"status": "ready"}


def create_app() -> FastAPI:
    app = FastAPI(title="casework-sandbox")
    app.add_api_route("/healthz", healthz, methods=["GET"])
    app.add_api_route("/readyz", readyz, methods=["GET"])
    return app


app = create_app()
