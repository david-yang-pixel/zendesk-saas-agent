"""FastAPI surface for the deliberately small management console."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from starlette.staticfiles import StaticFiles

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


class ConfigUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: str = Field(min_length=1)
    config: AdminConfig


def _default_config() -> AdminConfig:
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


def create_app(repository: ConfigRepository | None = None) -> FastAPI:
    configs = repository or InMemoryConfigRepository(initial=_default_config())
    service = AdminConfigService(repository=configs)
    app = FastAPI(title="casework-admin")

    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    async def current_config() -> ConfigSnapshot:
        return await service.current()

    async def sandbox_policy() -> dict[str, object]:
        return service.fixed_sandbox_policy()

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
    app.add_api_route(
        "/api/config", current_config, methods=["GET"], response_model=ConfigSnapshot
    )
    app.add_api_route(
        "/api/config/sandbox-policy", sandbox_policy, methods=["GET"]
    )
    app.add_api_route(
        "/api/config", update_config, methods=["PUT"], response_model=ConfigSnapshot
    )
    web_dist_value = os.environ.get("ADMIN_WEB_DIST")
    web_dist = Path(web_dist_value) if web_dist_value else None
    if web_dist is not None and web_dist.is_dir():
        app.mount("/", StaticFiles(directory=web_dist, html=True), name="admin-web")
    return app
