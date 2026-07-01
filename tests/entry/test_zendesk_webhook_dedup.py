"""Test handle_zendesk_webhook dedup behavior.

Regression for #2278: messaging tickets carry a static placeholder
``latest_comment_id``, so the old `{ticket_id}:{latest_comment_id}` dedup
key swallowed every webhook after the first one for the same ticket. The
new implementation keys on `sha256(body)` (delivery-unique), so distinct
events with the same `latest_comment_id` are still all forwarded to the
trigger consumer.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
from unittest.mock import MagicMock, patch

import pytest
from aiohttp import web

from zd_agent.services.zendesk_webhook import handle_zendesk_webhook
from zd_agent.services.app_context import install_saas_app_context, trigger_queue
from zd_agent.services.webhook_dedup import WebhookDedup


def _sign(secret: str, ts: str, body: bytes) -> str:
    mac = hmac.new(
        secret.encode("utf-8"),
        ts.encode("utf-8") + body,
        hashlib.sha256,
    )
    return base64.b64encode(mac.digest()).decode("utf-8")


def _build_app(tmp_path) -> web.Application:
    app = web.Application()
    cfg = MagicMock()
    cfg.webhook.signing_secret = "topsecret"
    cfg.webhook.zendesk_path = "/webhook/zendesk"
    cfg.public_reply.silent_initial_wait_seconds = 60

    import asyncio
    install_saas_app_context(
        app=app,
        cfg=cfg,
        trigger_queue=asyncio.Queue(),
        state=MagicMock(),
        webhook_dedup=WebhookDedup(tmp_path / "dedup.json", max_size=100),
        manager=MagicMock(),
    )
    return app


async def _post(app: web.Application, body: dict) -> web.Response:
    """Drive handle_zendesk_webhook with a synthetic signed request."""
    raw = json.dumps(body).encode("utf-8")
    ts = "1700000000"
    sig = _sign("topsecret", ts, raw)

    # Minimal Request stand-in: aiohttp lets us go through Application but
    # easier to just build a Request via web.Request.clone? We just call the
    # handler with a tiny shim object that exposes what the handler reads.
    class _Req:
        def __init__(self):
            self.headers = {
                "X-Zendesk-Webhook-Signature": sig,
                "X-Zendesk-Webhook-Signature-Timestamp": ts,
            }
            self.app = app

        async def read(self):
            return raw

    return await handle_zendesk_webhook(_Req())  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_same_body_deduped(tmp_path):
    """Identical webhook delivery (Zendesk retry) is deduped."""
    app = _build_app(tmp_path)
    body = {"ticket_id": 2278, "latest_comment_id": 48640250132756}

    r1 = await _post(app, body)
    r2 = await _post(app, body)

    assert r1.status == 200
    assert r2.status == 200
    assert r2.text == "dup"
    # Only first delivery enqueued
    assert trigger_queue(app).qsize() == 1


@pytest.mark.asyncio
async def test_different_body_same_comment_id_not_deduped(tmp_path):
    """#2278 regression: messaging webhooks share latest_comment_id but each
    delivery has a different body — must NOT be deduped."""
    app = _build_app(tmp_path)
    placeholder = 48640250132756

    body_a = {
        "ticket_id": 2278, "latest_comment_id": placeholder,
        "event_id": "evt-1",  # something distinguishing
    }
    body_b = {
        "ticket_id": 2278, "latest_comment_id": placeholder,
        "event_id": "evt-2",
    }
    body_c = {
        "ticket_id": 2278, "latest_comment_id": placeholder,
        "event_id": "evt-3",
    }

    r1 = await _post(app, body_a)
    r2 = await _post(app, body_b)
    r3 = await _post(app, body_c)

    assert r1.status == 200 and r1.text == "ok"
    assert r2.status == 200 and r2.text == "ok"
    assert r3.status == 200 and r3.text == "ok"
    # All three forwarded to trigger queue
    assert trigger_queue(app).qsize() == 3


@pytest.mark.asyncio
async def test_different_tickets_isolated(tmp_path):
    """Same body shape but different ticket_id are independent."""
    app = _build_app(tmp_path)

    r1 = await _post(app, {"ticket_id": 2278, "latest_comment_id": 1})
    r2 = await _post(app, {"ticket_id": 2280, "latest_comment_id": 1})

    assert r1.status == 200 and r1.text == "ok"
    assert r2.status == 200 and r2.text == "ok"
    assert trigger_queue(app).qsize() == 2


@pytest.mark.asyncio
async def test_missing_ticket_id_400(tmp_path):
    app = _build_app(tmp_path)
    r = await _post(app, {"some": "thing"})
    assert r.status == 400


@pytest.mark.asyncio
async def test_bad_signature_401(tmp_path):
    app = _build_app(tmp_path)

    class _Req:
        def __init__(self):
            self.headers = {
                "X-Zendesk-Webhook-Signature": "wrong",
                "X-Zendesk-Webhook-Signature-Timestamp": "1",
            }
            self.app = app

        async def read(self):
            return b"{}"

    r = await handle_zendesk_webhook(_Req())  # type: ignore[arg-type]
    assert r.status == 401


@pytest.mark.asyncio
async def test_classic_webhook_uses_initial_wait_window(tmp_path):
    app = _build_app(tmp_path)

    with patch("zd_agent.services.zendesk_webhook.time.time", return_value=1000.0):
        r = await _post(app, {"ticket_id": 2278, "latest_comment_id": 1})

    assert r.status == 200 and r.text == "ok"
    queued = trigger_queue(app).get_nowait()
    assert queued["available_at"] == 1060.0
