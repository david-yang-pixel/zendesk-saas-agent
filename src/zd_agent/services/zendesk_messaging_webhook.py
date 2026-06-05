"""Zendesk Messaging webhook handler."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
from typing import Any

from aiohttp import web

from zd_agent.log import get_logger
from zd_agent.services.app_context import app_config, trigger_queue, webhook_dedup

logger = get_logger(module="services.zendesk_messaging_webhook")

_CUSTOMER_ACTOR_TYPES = {"user", "end_user", "end-user"}


def verify_messaging_signature(
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


def _extract_ticket_id(event_payload: dict[str, Any]) -> str:
    """Best-effort ticket_id extraction from Zendesk Messaging payload."""
    detail = event_payload.get("detail") or {}
    if isinstance(detail, dict) and detail.get("id"):
        return str(detail["id"])

    subject = event_payload.get("subject") or ""
    if isinstance(subject, str) and subject.startswith("zen:ticket:"):
        return subject.split(":", 2)[-1]

    return ""


async def handle_zendesk_messaging_webhook(request: web.Request) -> web.Response:
    """aiohttp handler for `messaging_ticket.message_added` events."""
    cfg = app_config(request.app)
    secret = cfg.webhook.messaging_signing_secret or ""
    body = await request.read()

    sig = request.headers.get("X-Zendesk-Webhook-Signature", "")
    ts = request.headers.get("X-Zendesk-Webhook-Signature-Timestamp", "")

    if not verify_messaging_signature(secret=secret, timestamp=ts, body=body, signature=sig):
        logger.warning("zendesk_messaging_webhook_bad_signature")
        return web.Response(status=401, text="bad signature")

    try:
        event = json.loads(body)
    except Exception:
        logger.warning("zendesk_messaging_webhook_bad_json", body_preview=body[:200])
        return web.Response(status=400, text="bad json")

    event_id = event.get("id") or ""
    actor_type = (
        event.get("event", {}).get("actor", {}).get("type", "") or ""
    ).lower()
    msg_id = event.get("event", {}).get("message", {}).get("id") or ""
    ticket_id = _extract_ticket_id(event)

    if actor_type not in _CUSTOMER_ACTOR_TYPES:
        logger.info(
            "zendesk_messaging_webhook_skip_non_user",
            event_id=event_id, actor_type=actor_type,
            ticket_id=ticket_id, msg_id=msg_id,
        )
        return web.Response(status=200, text="skip non-user actor")

    if not ticket_id:
        logger.warning(
            "zendesk_messaging_webhook_missing_ticket_id",
            event_id=event_id, subject=event.get("subject"),
        )
        return web.Response(status=400, text="missing ticket_id")

    dedup = webhook_dedup(request.app)
    dedup_key = f"messaging:{event_id}"
    if dedup.seen(dedup_key):
        logger.info(
            "zendesk_messaging_webhook_dup",
            event_id=event_id, ticket_id=ticket_id, msg_id=msg_id,
        )
        return web.Response(status=200, text="dup")
    dedup.add(dedup_key)

    queue = trigger_queue(request.app)
    await queue.put({
        "source": "messaging_webhook",
        "ticket_id": ticket_id,
        "event": event,
    })
    logger.info(
        "zendesk_messaging_webhook_enqueued",
        event_id=event_id, ticket_id=ticket_id, msg_id=msg_id,
        body_preview=(event.get("event", {}).get("message", {}).get("body") or "")[:80],
    )
    return web.Response(status=200, text="ok")
