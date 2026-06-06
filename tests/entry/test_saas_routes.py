"""Tests for SaaS aiohttp route registration."""
from __future__ import annotations

from unittest.mock import MagicMock

from aiohttp import web

from zd_agent.services.saas_routes import register_saas_routes


def test_register_saas_routes_installs_expected_routes() -> None:
    app = web.Application()
    cfg = MagicMock()
    cfg.webhook.zendesk_path = "/webhook/zendesk"
    cfg.webhook.zendesk_messaging_path = "/webhook/zendesk-messaging"

    register_saas_routes(app=app, cfg=cfg)

    route_paths = {route.resource.canonical for route in app.router.routes()}
    assert "/webhook/zendesk" in route_paths
    assert "/webhook/zendesk-messaging" in route_paths
    assert "/healthz" in route_paths
    assert "/teleport-auth" not in route_paths
    assert "/internal/teleport-auth/issue" not in route_paths
    assert "/webhook/feishu" not in route_paths
