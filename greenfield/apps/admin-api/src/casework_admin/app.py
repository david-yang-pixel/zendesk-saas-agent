"""FastAPI surface for the deliberately small management console."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Protocol

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict, Field
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import JSONResponse
from starlette.staticfiles import StaticFiles
from starlette.types import Lifespan, Scope

from casework_admin.config import (
    AdminConfig,
    AdminConfigService,
    ConfigConflict,
    ConfigRepository,
    ConfigSnapshot,
    InMemoryConfigRepository,
    ModelConfig,
    ProcessingRulesConfig,
)
from casework_admin.read_models import (
    AdminReadRepository,
    ConnectionProbeResponse,
    InMemoryAdminReadRepository,
    OverviewResponse,
    RegistryResponse,
    RunListResponse,
    RunRecord,
    RunStatus,
)


class ConfigUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: str = Field(min_length=1)
    config: AdminConfig


class ReadinessPort(Protocol):
    async def check(self) -> bool: ...


class SPAStaticFiles(StaticFiles):
    """Serve the built console entry point for client-side history routes."""

    async def get_response(self, path: str, scope: Scope):
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as error:
            if (
                error.status_code != 404
                or scope.get("method") != "GET"
                or path.startswith("api/")
                or "." in Path(path).name
            ):
                raise
            return await super().get_response("index.html", scope)


def default_config() -> AdminConfig:
    return AdminConfig(
        desired_state="paused",
        root_model=ModelConfig(
            provider="unconfigured",
            model="unconfigured",
            credential_ref="unconfigured",
        ),
        rules=ProcessingRulesConfig(
            saas_tag="saas",
            saas_assignee="saas-support",
            reply_enabled=False,
        ),
    )


def create_app(
    repository: ConfigRepository | None = None,
    read_repository: AdminReadRepository | None = None,
    readiness: ReadinessPort | None = None,
    lifespan: Lifespan[FastAPI] | None = None,
) -> FastAPI:
    configs = repository or InMemoryConfigRepository(initial=default_config())
    reads = read_repository or InMemoryAdminReadRepository()
    service = AdminConfigService(repository=configs)
    app = FastAPI(title="casework-admin", lifespan=lifespan)

    async def validation_error(
        _request: Request, _error: Exception
    ) -> JSONResponse:
        # FastAPI's default 422 serializer includes the invalid ``input``
        # value.  That is unsafe for a rejected credential accidentally sent
        # to this boundary, so return only a stable, non-sensitive message.
        return JSONResponse(
            status_code=422,
            content={"detail": "invalid admin configuration"},
        )

    app.add_exception_handler(RequestValidationError, validation_error)

    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    async def readyz() -> dict[str, str]:
        if readiness is not None and not await readiness.check():
            raise HTTPException(status_code=503, detail="postgres_not_ready")
        return {"status": "ready"}

    async def current_config() -> ConfigSnapshot:
        return await service.current()

    async def sandbox_policy() -> dict[str, object]:
        return service.fixed_sandbox_policy()

    async def overview() -> OverviewResponse:
        current = await service.current()
        return await reads.overview(
            desired_state=current.config.desired_state,
            config_revision=current.revision,
        )

    async def runs(
        query: str | None = None,
        status: RunStatus | None = None,
    ) -> RunListResponse:
        return await reads.runs(query=query, status=status)

    async def run_detail(session_id: str) -> RunRecord:
        result = await reads.run(session_id=session_id)
        if result is None:
            raise HTTPException(status_code=404, detail="run not found")
        return result

    async def registry() -> RegistryResponse:
        return await reads.registry()

    async def probe_tool(tool_id: str) -> ConnectionProbeResponse:
        return await reads.probe_tool(tool_id=tool_id)

    async def update_config(
        request: ConfigUpdateRequest,
        actor: str = Header(alias="X-Admin-Actor", min_length=1),
    ) -> ConfigSnapshot:
        try:
            return await service.save(
                expected_revision=request.expected_revision,
                config=request.config,
                actor=actor,
            )
        except ConfigConflict as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    app.add_api_route("/healthz", healthz, methods=["GET"])
    app.add_api_route("/readyz", readyz, methods=["GET"])
    app.add_api_route(
        "/api/config", current_config, methods=["GET"], response_model=ConfigSnapshot
    )
    app.add_api_route(
        "/api/config/sandbox-policy", sandbox_policy, methods=["GET"]
    )
    app.add_api_route(
        "/api/overview", overview, methods=["GET"], response_model=OverviewResponse
    )
    app.add_api_route(
        "/api/runs", runs, methods=["GET"], response_model=RunListResponse
    )
    app.add_api_route(
        "/api/runs/{session_id}", run_detail, methods=["GET"], response_model=RunRecord
    )
    app.add_api_route(
        "/api/registry", registry, methods=["GET"], response_model=RegistryResponse
    )
    app.add_api_route(
        "/api/registry/tools/{tool_id}/test",
        probe_tool,
        methods=["POST"],
        response_model=ConnectionProbeResponse,
    )
    app.add_api_route(
        "/api/config", update_config, methods=["PUT"], response_model=ConfigSnapshot
    )
    web_dist_value = os.environ.get("ADMIN_WEB_DIST")
    web_dist = Path(web_dist_value) if web_dist_value else None
    if web_dist is not None and web_dist.is_dir():
        app.mount("/", SPAStaticFiles(directory=web_dist, html=True), name="admin-web")
    return app
