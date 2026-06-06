"""Standalone aiohttp app for DB Gateway."""
from __future__ import annotations

from typing import Any

from aiohttp import web

from zd_agent.config import get_config
from zd_agent.services.app_context import (
    CONFIG_KEY,
    TELEPORT_AUTH_TOKEN_STORE_KEY,
)
from zd_agent.services.db_gateway_core import execute_gateway_query
from zd_agent.services.teleport_auth_web import (
    handle_teleport_auth_get,
    handle_teleport_auth_issue,
    handle_teleport_auth_post,
)
from zd_agent.services.teleport_tokens import TokenStore


APP_CONFIG_KEY: web.AppKey[Any] = web.AppKey("zd_agent.db_gateway.config", object)


async def handle_healthz(request: web.Request) -> web.Response:
    return web.json_response({"ok": True, "service": "db-gateway"})


def _authorized(request: web.Request, cfg: Any) -> bool:
    if not cfg.db_gateway.api_key:
        return False
    return request.headers.get("X-API-Key", "") == cfg.db_gateway.api_key


async def handle_status(request: web.Request) -> web.Response:
    cfg = request.app[APP_CONFIG_KEY]
    if not _authorized(request, cfg):
        return web.json_response({"error": "unauthorized"}, status=401)
    return web.json_response({
        "ok": True,
        "postgres_host": cfg.postgres.host,
        "ports": {
            "prod": cfg.postgres.port_prod,
            "billing": cfg.postgres.port_billing,
            "plugin": cfg.postgres.port_plugin,
        },
        "teleport_proxy": cfg.teleport.proxy,
    })


async def handle_query(request: web.Request) -> web.Response:
    cfg = request.app[APP_CONFIG_KEY]
    if not _authorized(request, cfg):
        return web.json_response({"error": "unauthorized"}, status=401)

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400)

    result = await execute_gateway_query(
        cfg=cfg,
        database=str(body.get("database") or ""),
        sql=str(body.get("sql") or ""),
        ticket_id=str(body.get("ticket_id") or ""),
    )
    status = 200 if result.success else 400
    if result.code == "TELEPORT_EXPIRED":
        status = 503
    return web.json_response(result.to_json(), status=status)


def create_db_gateway_app(cfg: Any | None = None) -> web.Application:
    cfg = cfg or get_config()
    app = web.Application()
    app[APP_CONFIG_KEY] = cfg
    app[CONFIG_KEY] = cfg
    app[TELEPORT_AUTH_TOKEN_STORE_KEY] = TokenStore()
    app.router.add_get("/healthz", handle_healthz)
    app.router.add_get("/status", handle_status)
    app.router.add_post("/query", handle_query)
    app.router.add_get("/teleport-auth", handle_teleport_auth_get)
    app.router.add_post("/teleport-auth", handle_teleport_auth_post)
    app.router.add_post("/internal/teleport-auth/issue", handle_teleport_auth_issue)
    return app


def run_db_gateway_app() -> None:
    cfg = get_config()
    app = create_db_gateway_app(cfg)
    web.run_app(
        app,
        host=cfg.db_gateway.listen_host,
        port=cfg.db_gateway.listen_port,
    )
