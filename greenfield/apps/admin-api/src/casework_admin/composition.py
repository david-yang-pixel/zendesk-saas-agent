"""Production composition root for the Admin API process."""

from __future__ import annotations

import json
import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import cast

from fastapi import FastAPI

from casework_admin.app import create_app as create_admin_app
from casework_admin.app import default_config
from casework_admin.postgres_config import (
    PostgresConfigRepository,
    postgres_gateway_scope,
)
from casework_admin.postgres_reads import (
    PostgresAdminReadRepository,
    postgres_read_transaction_scope,
)
from casework_admin.tool_probes import HttpToolProbe
from casework_persistence import (
    SCHEMA_HEAD,
    DatabaseReadiness,
    create_engine,
    create_session_factory,
)


def tool_probe_from_environment() -> HttpToolProbe | None:
    """Build optional HTTPS-only probes without accepting any credential data."""
    raw = os.environ.get("CASEWORK_TOOL_PROBE_ENDPOINTS", "").strip()
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as error:
        raise RuntimeError(
            "CASEWORK_TOOL_PROBE_ENDPOINTS must be valid JSON"
        ) from error
    if not isinstance(payload, dict):
        raise RuntimeError("CASEWORK_TOOL_PROBE_ENDPOINTS must map tool ids to URLs")
    entries = cast(dict[object, object], payload)
    if not all(
        isinstance(tool_id, str) and isinstance(endpoint, str)
        for tool_id, endpoint in entries.items()
    ):
        raise RuntimeError("CASEWORK_TOOL_PROBE_ENDPOINTS must map tool ids to URLs")
    endpoints = {
        cast(str, tool_id): cast(str, endpoint)
        for tool_id, endpoint in entries.items()
    }
    return HttpToolProbe(endpoints)


def create_app() -> FastAPI:
    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError("DATABASE_URL is required for the production Admin API")

    engine = create_engine(database_url)
    sessions = create_session_factory(engine)
    readiness = DatabaseReadiness(sessions, expected_revision=SCHEMA_HEAD)
    configs = PostgresConfigRepository(
        gateway_scope=postgres_gateway_scope(sessions)
    )
    tool_probe = tool_probe_from_environment()
    reads = PostgresAdminReadRepository(
        transaction_scope=postgres_read_transaction_scope(sessions),
        tool_probe=tool_probe,
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
        try:
            if not await readiness.check():
                raise RuntimeError("PostgreSQL schema is not ready")
            await configs.bootstrap(initial=default_config())
            yield
        finally:
            if tool_probe is not None:
                await tool_probe.aclose()
            await engine.dispose()

    return create_admin_app(
        repository=configs,
        read_repository=reads,
        readiness=readiness,
        lifespan=lifespan,
    )


__all__ = ["create_app", "tool_probe_from_environment"]
