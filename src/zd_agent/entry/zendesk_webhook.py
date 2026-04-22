"""Zendesk webhook: signature verification + aiohttp handler."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json

from aiohttp import web

from zd_agent.log import get_logger

logger = get_logger(module="entry.zendesk_webhook")


def verify_zendesk_signature(
    *, secret: str, timestamp: str, body: bytes, signature: str,
) -> bool:
    """Verify HMAC-SHA256 signature per Zendesk webhook spec.

    Signed payload = bytes(timestamp) + body; secret is the shared webhook secret.
    Returns True if the provided signature matches; False otherwise.
    Uses hmac.compare_digest for timing-safe comparison.
    """
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
    """aiohttp handler for Zendesk webhook events.

    Expects:
      - app state: `config` (full AppConfig), `webhook_dedup`, `trigger_queue`
      - headers: X-Zendesk-Webhook-Signature, X-Zendesk-Webhook-Signature-Timestamp
      - body: JSON with at minimum `ticket_id` and optionally `latest_comment_id`
    """
    cfg = request.app["config"]
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

    comment_id = event.get("latest_comment_id")
    key = f"{ticket_id}:{comment_id}"
    dedup = request.app["webhook_dedup"]
    if dedup.seen(key):
        logger.info("zendesk_webhook_dup", key=key)
        return web.Response(status=200, text="dup")
    dedup.add(key)

    queue = request.app["trigger_queue"]
    await queue.put({
        "source": "zendesk_webhook",
        "ticket_id": ticket_id,
        "event": event,
    })
    return web.Response(status=200, text="ok")
