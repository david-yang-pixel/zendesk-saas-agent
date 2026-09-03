"""Production composition root for the Admin API process."""

from __future__ import annotations

import json
import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import cast

from fastapi import FastAPI

from casework_admin.app import create_app as create_admin_app
from casework_admin.app import default_config
from casework_admin.auth import AdminAuthStore
from casework_admin.model_credentials import (
    FileCredentialStore,
    ModelCredentialService,
    OpenAIResponsesCredentialProbe,
)
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
        cast(str, tool_id): cast(str, endpoint) for tool_id, endpoint in entries.items()
    }
    return HttpToolProbe(endpoints)


def create_app() -> FastAPI:
    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError("DATABASE_URL is required for the production Admin API")

    engine = create_engine(database_url)
    sessions = create_session_factory(engine)
    readiness = DatabaseReadiness(sessions, expected_revision=SCHEMA_HEAD)
    configs = PostgresConfigRepository(gateway_scope=postgres_gateway_scope(sessions))
    tool_probe = tool_probe_from_environment()
    reads = PostgresAdminReadRepository(
        transaction_scope=postgres_read_transaction_scope(sessions),
        tool_probe=tool_probe,
    )
    credential_file = os.environ.get("CASEWORK_CREDENTIAL_FILE", "").strip()
    model_base_url = os.environ.get("CASEWORK_MODEL_BASE_URL", "").strip()
    if not credential_file:
        raise RuntimeError("CASEWORK_CREDENTIAL_FILE is required for the Admin API")
    if not model_base_url:
        raise RuntimeError("CASEWORK_MODEL_BASE_URL is required for the Admin API")
    auth_file = os.environ.get("CASEWORK_ADMIN_AUTH_FILE", "").strip()
    internal_token_file = os.environ.get(
        "CASEWORK_ADMIN_INTERNAL_TOKEN_FILE", ""
    ).strip()
    public_origin = os.environ.get("CASEWORK_ADMIN_PUBLIC_ORIGIN", "").strip()
    if not auth_file:
        raise RuntimeError("CASEWORK_ADMIN_AUTH_FILE is required for the Admin API")
    if not internal_token_file:
        raise RuntimeError(
            "CASEWORK_ADMIN_INTERNAL_TOKEN_FILE is required for the Admin API"
        )
    if not public_origin.startswith("https://"):
        raise RuntimeError("CASEWORK_ADMIN_PUBLIC_ORIGIN must use HTTPS")
    internal_token = Path(internal_token_file).read_text(encoding="utf-8").strip()
    if len(internal_token) < 32:
        raise RuntimeError("Admin internal token is invalid")
    auth = AdminAuthStore(Path(auth_file))
    model_probe = OpenAIResponsesCredentialProbe(base_url=model_base_url)
    model_credentials = ModelCredentialService(
        store=FileCredentialStore(Path(credential_file)), probe=model_probe
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
            await model_probe.aclose()
            await engine.dispose()

    return create_admin_app(
        repository=configs,
        read_repository=reads,
        readiness=readiness,
        model_credentials=model_credentials,
        auth_store=auth,
        internal_token=internal_token,
        public_origin=public_origin,
        lifespan=lifespan,
    )


__all__ = ["create_app", "tool_probe_from_environment"]
