"""Zendesk Messaging webhook: receives `messaging_ticket.message_added` events.

Why a separate handler from `zendesk_webhook.py`
------------------------------------------------
The legacy `zendesk_webhook.py` consumes Zendesk's `conditional_ticket_events`
payload (Triggers/Automations 风格的自定义 JSON template). This module receives
Zendesk's *platform* messaging webhook —— a fixed schema for the
`zen:event-type:messaging_ticket.message_added` event:

    {
        "account_id": 21825834,
        "detail": {"id": "141"},                   ← ticket_id
        "event": {
            "actor": {"id": "...", "type": "user"|"system"|"agent"},
            "conversation_id": "...",
            "message": {"id": "...", "body": "..."}
        },
        "id": "01JQMQH83YNAKWSWJ8B1QH2NSC",        ← event id (used as dedup key)
        "subject": "zen:ticket:141",
        "time": "...",
        "type": "zen:event-type:messaging_ticket.message_added",
        "zendesk_event_version": "2022-11-06"
    }

Filtering rules
---------------
- Only `actor.type == "user"` is forwarded — SE replies / answer-bot / system
  messages should not re-trigger the agent.
- Dedup uses the top-level event `id` (delivery-unique).
- `ticket_id` is read from `detail.id`; falls back to parsing `subject`
  (`zen:ticket:<id>`) if `detail.id` is missing.

The webhook itself signs with a *separate* signing secret distinct from the
classic ticket webhook. Configured via env `ZENDESK_MESSAGING_WEBHOOK_SECRET`.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
from typing import Any

from aiohttp import web

from zd_agent.log import get_logger

logger = get_logger(module="entry.zendesk_messaging_webhook")


def verify_messaging_signature(
    *, secret: str, timestamp: str, body: bytes, signature: str,
) -> bool:
    """Verify HMAC-SHA256 signature per Zendesk webhook spec.

    Same algo as classic Tickets webhook (`zendesk_webhook.verify_zendesk_signature`),
    just with a *different* secret. Kept as a separate function to avoid
    accidental cross-secret use.
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


def _extract_ticket_id(event_payload: dict[str, Any]) -> str:
    """Best-effort ticket_id extraction.

    Primary: `detail.id` (string).
    Fallback: parse `subject` of form `zen:ticket:<id>`.
    Returns "" if neither is usable.
    """
    detail = event_payload.get("detail") or {}
    if isinstance(detail, dict) and detail.get("id"):
        return str(detail["id"])

    subject = event_payload.get("subject") or ""
    if isinstance(subject, str) and subject.startswith("zen:ticket:"):
        return subject.split(":", 2)[-1]

    return ""


async def handle_zendesk_messaging_webhook(request: web.Request) -> web.Response:
    """aiohttp handler for `messaging_ticket.message_added` events.

    Filters non-user actors, dedups by event id, then enqueues a unified
    trigger_queue message that `_trigger_consumer` already knows how to handle
    (source="messaging_webhook" routes through the same downstream pipeline as
    legacy `zendesk_webhook` events).
    """
    cfg = request.app["config"]
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

    # Filter: only customer (end-user) messages re-trigger the agent.
    # Zendesk's actor.type values include "user" (end-user), "agent" (SE),
    # "system" / "zd:answerBot" etc. Anything that isn't a "user" message
    # is ack'd 200 and dropped.
    if actor_type != "user":
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

    # Dedup: use the platform event id (globally unique per delivery). Reuses
    # the same WebhookDedup store as the classic Tickets webhook so we get
    # one combined dedup window across both sources.
    dedup = request.app["webhook_dedup"]
    dedup_key = f"messaging:{event_id}"
    if dedup.seen(dedup_key):
        logger.info(
            "zendesk_messaging_webhook_dup",
            event_id=event_id, ticket_id=ticket_id, msg_id=msg_id,
        )
        return web.Response(status=200, text="dup")
    dedup.add(dedup_key)

    queue = request.app["trigger_queue"]
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
