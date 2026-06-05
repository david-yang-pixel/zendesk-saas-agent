"""Route registration for the SaaS aiohttp app."""
from __future__ import annotations

from typing import Any

from aiohttp import web

from zd_agent.services.teleport_auth_web import (
    handle_teleport_auth_get,
    handle_teleport_auth_issue,
    handle_teleport_auth_post,
)
from zd_agent.services.zendesk_messaging_webhook import (
    handle_zendesk_messaging_webhook,
)
from zd_agent.services.zendesk_webhook import handle_zendesk_webhook


async def handle_healthz(request: web.Request) -> web.Response:
    return web.Response(status=200, text="ok")


def register_saas_routes(*, app: web.Application, cfg: Any) -> None:
    """Register all HTTP routes exposed by the SaaS service."""
    app.router.add_post(cfg.webhook.zendesk_path, handle_zendesk_webhook)
    app.router.add_post(
        cfg.webhook.zendesk_messaging_path,
        handle_zendesk_messaging_webhook,
    )
    app.router.add_get("/healthz", handle_healthz)
    app.router.add_get("/teleport-auth", handle_teleport_auth_get)
    app.router.add_post("/teleport-auth", handle_teleport_auth_post)
    app.router.add_post("/internal/teleport-auth/issue", handle_teleport_auth_issue)
