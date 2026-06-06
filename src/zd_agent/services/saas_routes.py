"""Route registration for the SaaS aiohttp app."""
from __future__ import annotations

from typing import Any

from aiohttp import web

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
