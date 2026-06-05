"""Zendesk webhook: signature verification + aiohttp handler."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json

from aiohttp import web

from zd_agent.log import get_logger
from zd_agent.services.app_context import app_config, trigger_queue, webhook_dedup

logger = get_logger(module="services.zendesk_webhook")


def verify_zendesk_signature(
    *, secret: str, timestamp: str, body: bytes, signature: str,
) -> bool:
    """Verify HMAC-SHA256 signature per Zendesk webhook spec."""
    if not secret or not timestamp or signature is None:
        return False
    mac = hmac.new(
        secret.encode("utf-8"),
        timestamp.encode("utf-8") + body,
        hashlib.sha256,
    )
    expected = base64.b64encode(mac.digest()).decode("utf-8")
    return hmac.compare_digest(expected, signature)


async def handle_zendesk_webhook(request: web.Request) -> web.Response:
    """aiohttp handler for Zendesk webhook events."""
    cfg = app_config(request.app)
    secret = cfg.webhook.signing_secret or ""
    body = await request.read()
    sig = request.headers.get("X-Zendesk-Webhook-Signature", "")
    ts = request.headers.get("X-Zendesk-Webhook-Signature-Timestamp", "")

    if not verify_zendesk_signature(secret=secret, timestamp=ts, body=body, signature=sig):
        logger.warning("zendesk_webhook_bad_signature")
        return web.Response(status=401, text="bad signature")

    try:
        event = json.loads(body)
    except Exception:
        return web.Response(status=400, text="bad json")

    ticket_id = str(event.get("ticket_id") or "")
    if not ticket_id:
        return web.Response(status=400, text="missing ticket_id")

    body_hash = hashlib.sha256(body).hexdigest()[:16]
    comment_id = event.get("latest_comment_id")
    key = f"{ticket_id}:{body_hash}"
    dedup = webhook_dedup(request.app)
    if dedup.seen(key):
        logger.info("zendesk_webhook_dup", key=key, comment_id=comment_id)
        return web.Response(status=200, text="dup")
    dedup.add(key)

    queue = trigger_queue(request.app)
    await queue.put({
        "source": "zendesk_webhook",
        "ticket_id": ticket_id,
        "event": event,
    })
    return web.Response(status=200, text="ok")
