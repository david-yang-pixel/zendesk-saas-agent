"""Production composition root for the Admin API process."""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from casework_admin.app import create_app as create_admin_app
from casework_admin.app import default_config
from casework_admin.postgres_config import (
    PostgresConfigRepository,
    postgres_gateway_scope,
)
from casework_persistence import (
    SCHEMA_HEAD,
    DatabaseReadiness,
    create_engine,
    create_session_factory,
)


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

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
        if not await readiness.check():
            raise RuntimeError("PostgreSQL schema is not ready")
        await configs.bootstrap(initial=default_config())
        try:
            yield
        finally:
            await engine.dispose()

    return create_admin_app(
        repository=configs,
        readiness=readiness,
        lifespan=lifespan,
    )


__all__ = ["create_app"]
