"""Zendesk Messaging webhook handler."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any

from aiohttp import web

from zd_agent.log import get_logger
from zd_agent.services.app_context import app_config, trigger_queue, webhook_dedup
from zd_agent.services.messaging_context import save_messaging_context
from zd_agent.services.trigger_delay import with_trigger_delay

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


def _extract_conversation_id(event_payload: dict[str, Any]) -> str:
    """Best-effort conversation id extraction from Zendesk Messaging payload."""
    event = event_payload.get("event") or {}
    detail = event_payload.get("detail") or {}
    if isinstance(event, dict):
        conversation = event.get("conversation") or {}
        if isinstance(conversation, dict) and conversation.get("id"):
            return str(conversation["id"])
        if event.get("conversation_id"):
            return str(event["conversation_id"])
    if isinstance(detail, dict) and detail.get("conversation_id"):
        return str(detail["conversation_id"])
    if event_payload.get("conversation_id"):
        return str(event_payload["conversation_id"])
    return ""


def _extract_messaging_app_id(event_payload: dict[str, Any]) -> str:
    """Best-effort Messaging app id extraction from Zendesk webhook payload."""
    top_app = event_payload.get("app") or {}
    if isinstance(top_app, dict) and top_app.get("id"):
        return str(top_app["id"])
    event = event_payload.get("event") or {}
    if isinstance(event, dict):
        event_app = event.get("app") or {}
        if isinstance(event_app, dict) and event_app.get("id"):
            return str(event_app["id"])
        if event.get("app_id"):
            return str(event["app_id"])
    detail = event_payload.get("detail") or {}
    if isinstance(detail, dict) and detail.get("app_id"):
        return str(detail["app_id"])
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
    conversation_id = _extract_conversation_id(event)
    messaging_app_id = _extract_messaging_app_id(event)

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

    try:
        save_messaging_context(
            ticket_id=ticket_id,
            conversation_id=conversation_id,
            app_id=messaging_app_id,
        )
    except Exception as e:
        logger.warning(
            "messaging_context_save_failed",
            ticket_id=ticket_id,
            error=str(e)[:200],
        )

    queue = trigger_queue(request.app)
    await queue.put(
        with_trigger_delay(
            {
                "source": "messaging_webhook",
                "ticket_id": ticket_id,
                "conversation_id": conversation_id,
                "messaging_app_id": messaging_app_id,
                "event": event,
            },
            delay_seconds=cfg.public_reply.silence_window_seconds,
            now_fn=time.time,
        )
    )
    logger.info(
        "zendesk_messaging_webhook_enqueued",
        event_id=event_id, ticket_id=ticket_id, msg_id=msg_id,
        has_conversation_id=bool(conversation_id),
        has_messaging_app_id=bool(messaging_app_id),
        body_preview=(event.get("event", {}).get("message", {}).get("body") or "")[:80],
    )
    return web.Response(status=200, text="ok")
