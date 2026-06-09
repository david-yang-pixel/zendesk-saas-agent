"""Tests for SaaS aiohttp route registration."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from aiohttp import web

from zd_agent.services.app_context import install_saas_app_context
from zd_agent.services.saas_routes import handle_pre_send_freshness, register_saas_routes


def test_register_saas_routes_installs_expected_routes() -> None:
    app = web.Application()
    cfg = MagicMock()
    cfg.webhook.zendesk_path = "/webhook/zendesk"
    cfg.webhook.zendesk_messaging_path = "/webhook/zendesk-messaging"

    register_saas_routes(app=app, cfg=cfg)

    route_paths = {route.resource.canonical for route in app.router.routes()}
    assert "/webhook/zendesk" in route_paths
    assert "/webhook/zendesk-messaging" in route_paths
    assert "/internal/pre-send-freshness" in route_paths
    assert "/healthz" in route_paths
    assert "/teleport-auth" not in route_paths
    assert "/internal/teleport-auth/issue" not in route_paths
    assert "/webhook/feishu" not in route_paths


class _Request:
    def __init__(self, *, app, headers, payload):
        self.app = app
        self.headers = headers
        self._payload = payload

    async def json(self):
        return self._payload


@pytest.mark.asyncio
async def test_pre_send_freshness_rejects_bad_api_key() -> None:
    app = web.Application()
    cfg = MagicMock()
    cfg.public_reply.browser_relay_api_key = "relay-secret"
    install_saas_app_context(
        app=app,
        cfg=cfg,
        manager=MagicMock(),
        trigger_queue=MagicMock(),
        state=MagicMock(),
        webhook_dedup=MagicMock(),
    )
    request = _Request(
        app=app,
        headers={"Authorization": "Bearer wrong"},
        payload={
            "ticket_id": 1,
            "snapshot_customer_message_count": 1,
        },
    )

    response = await handle_pre_send_freshness(request)

    assert response.status == 401
