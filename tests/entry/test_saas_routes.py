"""Tests for SaaS aiohttp route registration."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import web

from zd_agent.services.app_context import install_saas_app_context
from zd_agent.services.saas_routes import (
    handle_agent_config,
    handle_agent_models,
    handle_pre_send_freshness,
    register_saas_routes,
)


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
    assert "/agent/config" in route_paths
    assert "/agent/models" in route_paths
    assert "/agent/tools" in route_paths
    assert "/healthz" in route_paths
    assert "/teleport-auth" not in route_paths
    assert "/internal/teleport-auth/issue" not in route_paths
    assert "/webhook/feishu" not in route_paths


class _Request:
    def __init__(self, *, app, headers, payload, query=None):
        self.app = app
        self.headers = headers
        self._payload = payload
        self.query = query or {}

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


@pytest.mark.asyncio
async def test_pre_send_freshness_blocks_when_ticket_fetch_returns_none(
    monkeypatch,
) -> None:
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
    monkeypatch.setattr(
        "zd_agent.services.ticket_messages._fetch_customer_messages",
        AsyncMock(return_value=([], None)),
    )
    request = _Request(
        app=app,
        headers={"Authorization": "Bearer relay-secret"},
        payload={
            "ticket_id": 1,
            "snapshot_customer_message_count": 0,
        },
    )

    response = await handle_pre_send_freshness(request)
    payload = json.loads(response.text)

    assert response.status == 200
    assert payload["status"] == "stale_context"
    assert payload["continue_send"] is False
    assert payload["reason"] == "fetch_error: RuntimeError"


@pytest.mark.asyncio
async def test_agent_config_rejects_bad_api_key(monkeypatch) -> None:
    class _Env:
        hc_client_api_key = "hc-secret"

    monkeypatch.setattr("zd_agent.services.saas_routes.EnvSettings", lambda: _Env())

    app = web.Application()
    cfg = MagicMock()
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
        payload={},
    )

    response = await handle_agent_config(request)

    assert response.status == 401


@pytest.mark.asyncio
async def test_agent_models_passes_model_key_and_query(monkeypatch) -> None:
    class _Env:
        hc_client_api_key = "hc-secret"
        openai_api_key = "env-model-key"
        openai_base_url = None

    monkeypatch.setattr("zd_agent.services.saas_routes.EnvSettings", lambda: _Env())
    list_models = AsyncMock(return_value={"count": 1, "models": [{"id": "gpt-5.5"}]})
    monkeypatch.setattr("zd_agent.services.saas_routes.list_agent_models", list_models)

    app = web.Application()
    cfg = MagicMock()
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
        headers={
            "Authorization": "Bearer hc-secret",
            "X-Agent-Model-Api-Key": "user-model-key",
        },
        payload={},
        query={"target": "subagent", "base_url": "https://llm.example.test/v1"},
    )

    response = await handle_agent_models(request)

    assert response.status == 200
    list_models.assert_awaited_once()
    assert list_models.await_args.kwargs["target"] == "subagent"
    assert list_models.await_args.kwargs["base_url"] == "https://llm.example.test/v1"
    assert list_models.await_args.kwargs["api_key"] == "user-model-key"
