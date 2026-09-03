"""FastAPI surface for the deliberately small management console."""

from __future__ import annotations

import os
import secrets
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Protocol

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict, Field
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import JSONResponse
from starlette.staticfiles import StaticFiles
from starlette.types import Lifespan, Scope

from casework_admin.auth import (
    SESSION_TTL_SECONDS,
    AdminAuthStore,
    AdminSession,
    safe_remote_ip,
)
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
from casework_admin.model_credentials import (
    ModelCatalogRequest,
    ModelCatalogResponse,
    ModelCredentialList,
    ModelCredentialNotConfigured,
    ModelCredentialReplaceRequest,
    ModelCredentialReplaceResponse,
    ModelCredentialService,
    ModelCredentialValidationError,
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


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=256)


class PasswordChangeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=12, max_length=256)


class SessionResponse(BaseModel):
    username: str
    csrf_token: str
    force_password_change: bool


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
            base_url="https://api.openai.com",
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
    model_credentials: ModelCredentialService | None = None,
    auth_store: AdminAuthStore | None = None,
    internal_token: str | None = None,
    public_origin: str = "https://zendesk.smlershou.top",
    session_cookie_path: str = "/admin",
    secure_cookie: bool = True,
    lifespan: Lifespan[FastAPI] | None = None,
) -> FastAPI:
    configs = repository or InMemoryConfigRepository(initial=default_config())
    reads = read_repository or InMemoryAdminReadRepository()
    service = AdminConfigService(repository=configs)
    app = FastAPI(title="casework-admin", lifespan=lifespan)
    session_cookie = "casework_admin_session"

    def session_payload(session: AdminSession) -> SessionResponse:
        return SessionResponse(
            username=session.username,
            csrf_token=session.csrf_token,
            force_password_change=session.force_password_change,
        )

    def current_session(request: Request) -> AdminSession:
        session = getattr(request.state, "admin_session", None)
        if not isinstance(session, AdminSession):
            raise HTTPException(status_code=401, detail="authentication required")
        return session

    @app.middleware("http")
    async def protect_admin_api(  # pyright: ignore[reportUnusedFunction]
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if auth_store is not None and request.url.path.startswith("/api/"):
            authorization = request.headers.get("authorization", "")
            supplied_internal = (
                authorization[7:] if authorization.lower().startswith("bearer ") else ""
            )
            is_internal = bool(
                internal_token
                and supplied_internal
                and secrets.compare_digest(supplied_internal, internal_token)
            )
            if is_internal:
                request.state.admin_actor = "production-cutover"
                request.state.admin_internal = True
            else:
                public_login = request.url.path == "/api/auth/login"
                session = auth_store.validate_session(
                    request.cookies.get(session_cookie)
                )
                if public_login:
                    if request.headers.get("origin") != public_origin:
                        return JSONResponse(
                            status_code=403, content={"detail": "invalid origin"}
                        )
                elif session is None:
                    return JSONResponse(
                        status_code=401,
                        content={"detail": "authentication required"},
                    )
                else:
                    request.state.admin_session = session
                    request.state.admin_actor = session.username
                    if session.force_password_change and request.url.path not in {
                        "/api/auth/session",
                        "/api/auth/logout",
                        "/api/auth/change-password",
                    }:
                        return JSONResponse(
                            status_code=403,
                            content={"detail": "password change required"},
                        )
                    if request.method not in {"GET", "HEAD", "OPTIONS"}:
                        if request.headers.get("origin") != public_origin:
                            return JSONResponse(
                                status_code=403,
                                content={"detail": "invalid origin"},
                            )
                        supplied_csrf = request.headers.get("x-csrf-token", "")
                        if not secrets.compare_digest(
                            supplied_csrf, session.csrf_token
                        ):
                            return JSONResponse(
                                status_code=403,
                                content={"detail": "invalid csrf token"},
                            )
        response = await call_next(request)
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("Cache-Control", "no-store")
        return response

    async def validation_error(_request: Request, _error: Exception) -> JSONResponse:
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
        request: ConfigUpdateRequest, raw: Request
    ) -> ConfigSnapshot:
        try:
            return await service.save(
                expected_revision=request.expected_revision,
                config=request.config,
                actor=str(
                    getattr(
                        raw.state,
                        "admin_actor",
                        raw.headers.get("x-admin-actor", "local-test"),
                    )
                ),
            )
        except ConfigConflict as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    async def list_model_credentials() -> ModelCredentialList:
        if model_credentials is None:
            raise HTTPException(
                status_code=503, detail="model credential store unavailable"
            )
        current = await service.current()
        try:
            return await model_credentials.list(current.config)
        except ValueError as error:
            raise HTTPException(
                status_code=409, detail="model credential references are invalid"
            ) from error

    async def replace_model_credential(
        request: ModelCredentialReplaceRequest,
    ) -> ModelCredentialReplaceResponse:
        if model_credentials is None:
            raise HTTPException(
                status_code=503, detail="model credential store unavailable"
            )
        current = await service.current()
        try:
            return await model_credentials.replace(
                config=current.config,
                credential_ref=request.credential_ref,
                secret=request.secret,
            )
        except ModelCredentialNotConfigured as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ModelCredentialValidationError as error:
            code = error.result.error_code or "unavailable"
            raise HTTPException(
                status_code=422,
                detail=f"model credential validation failed: {code}",
            ) from error
        except ValueError as error:
            raise HTTPException(
                status_code=422, detail="invalid model credential"
            ) from error

    async def discover_models(request: ModelCatalogRequest) -> ModelCatalogResponse:
        if model_credentials is None:
            raise HTTPException(
                status_code=503, detail="model credential store unavailable"
            )
        try:
            return await model_credentials.discover(
                provider=request.provider,
                base_url=request.base_url,
                secret=request.secret,
            )
        except ModelCredentialValidationError as error:
            code = error.result.error_code or "unavailable"
            raise HTTPException(
                status_code=422, detail=f"model discovery failed: {code}"
            ) from error
        except ValueError as error:
            raise HTTPException(
                status_code=422, detail="invalid model endpoint"
            ) from error

    async def login(
        request: LoginRequest, raw: Request, response: Response
    ) -> SessionResponse:
        if auth_store is None:
            raise HTTPException(status_code=503, detail="authentication unavailable")
        fallback = raw.client.host if raw.client is not None else "unknown"
        result = auth_store.login(
            username=request.username,
            password=request.password,
            remote_ip=safe_remote_ip(dict(raw.headers), fallback),
        )
        if result.rate_limited:
            raise HTTPException(
                status_code=429,
                detail="too many login attempts",
                headers={"Retry-After": str(result.retry_after_seconds)},
            )
        if result.session_token is None or result.session is None:
            raise HTTPException(status_code=401, detail="invalid username or password")
        response.set_cookie(
            session_cookie,
            result.session_token,
            max_age=SESSION_TTL_SECONDS,
            path=session_cookie_path,
            secure=secure_cookie,
            httponly=True,
            samesite="strict",
        )
        return session_payload(result.session)

    async def auth_session(request: Request) -> SessionResponse:
        return session_payload(current_session(request))

    async def logout(request: Request, response: Response) -> dict[str, str]:
        if auth_store is None:
            raise HTTPException(status_code=503, detail="authentication unavailable")
        auth_store.revoke_session(request.cookies.get(session_cookie))
        response.delete_cookie(session_cookie, path=session_cookie_path)
        return {"status": "signed_out"}

    async def change_password(
        request: PasswordChangeRequest, raw: Request, response: Response
    ) -> dict[str, str]:
        if auth_store is None:
            raise HTTPException(status_code=503, detail="authentication unavailable")
        session = current_session(raw)
        if not auth_store.change_password(
            username=session.username,
            current_password=request.current_password,
            new_password=request.new_password,
        ):
            raise HTTPException(status_code=422, detail="password change rejected")
        response.delete_cookie(session_cookie, path=session_cookie_path)
        return {"status": "password_changed"}

    app.add_api_route("/healthz", healthz, methods=["GET"])
    app.add_api_route("/readyz", readyz, methods=["GET"])
    app.add_api_route(
        "/api/auth/login", login, methods=["POST"], response_model=SessionResponse
    )
    app.add_api_route(
        "/api/auth/session",
        auth_session,
        methods=["GET"],
        response_model=SessionResponse,
    )
    app.add_api_route("/api/auth/logout", logout, methods=["POST"])
    app.add_api_route("/api/auth/change-password", change_password, methods=["POST"])
    app.add_api_route(
        "/api/config", current_config, methods=["GET"], response_model=ConfigSnapshot
    )
    app.add_api_route("/api/config/sandbox-policy", sandbox_policy, methods=["GET"])
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
    app.add_api_route(
        "/api/model-credentials",
        list_model_credentials,
        methods=["GET"],
        response_model=ModelCredentialList,
    )
    app.add_api_route(
        "/api/model-credentials",
        replace_model_credential,
        methods=["PUT"],
        response_model=ModelCredentialReplaceResponse,
    )
    app.add_api_route(
        "/api/model-catalog",
        discover_models,
        methods=["POST"],
        response_model=ModelCatalogResponse,
    )
    tx_scope = getattr(reads, "_transaction_scope", None)
    if tx_scope is not None:
        try:
            from .api_v2 import register_modern_routes
            register_modern_routes(app, transaction_scope=tx_scope)
        except Exception as exc:
            import logging
            logging.getLogger("uvicorn.error").warning("Could not register modern routes: %s", exc)

    web_dist_value = os.environ.get("ADMIN_WEB_DIST")
    web_dist = Path(web_dist_value) if web_dist_value else None
    if web_dist is not None and web_dist.is_dir():
        app.mount("/", SPAStaticFiles(directory=web_dist, html=True), name="admin-web")
    return app
