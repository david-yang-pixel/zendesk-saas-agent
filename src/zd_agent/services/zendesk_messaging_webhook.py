"""Zendesk Messaging webhook handler."""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any

from aiohttp import web

from zd_agent.log import get_logger
from zd_agent.services.app_context import (
    app_config,
    session_manager,
    trigger_queue,
    webhook_dedup,
)
from zd_agent.services.messaging_context import save_messaging_context
from zd_agent.services.trigger_delay import with_trigger_delay
from zd_agent.services.zendesk_webhook import verify_zendesk_signature

logger = get_logger(module="services.zendesk_messaging_webhook")

_CUSTOMER_ACTOR_TYPES = {"user", "end_user", "end-user"}


def verify_messaging_signature(
    *,
    secret: str,
    timestamp: str,
    body: bytes,
    signature: str,
) -> bool:
    """Verify HMAC-SHA256 signature per Zendesk webhook spec."""
    return verify_zendesk_signature(
        secret=secret,
        timestamp=timestamp,
        body=body,
        signature=signature,
    )


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

    if not verify_messaging_signature(
        secret=secret, timestamp=ts, body=body, signature=sig
    ):
        logger.warning("zendesk_messaging_webhook_bad_signature")
        return web.Response(status=401, text="bad signature")

    try:
        event = json.loads(body)
    except Exception:
        logger.warning(
            "zendesk_messaging_webhook_bad_json",
            body_size=len(body),
            body_sha256=hashlib.sha256(body).hexdigest(),
        )
        return web.Response(status=400, text="bad json")

    event_id = event.get("id") or ""
    actor_type = (event.get("event", {}).get("actor", {}).get("type", "") or "").lower()
    msg_id = event.get("event", {}).get("message", {}).get("id") or ""
    ticket_id = _extract_ticket_id(event)
    conversation_id = _extract_conversation_id(event)
    messaging_app_id = _extract_messaging_app_id(event)

    if actor_type not in _CUSTOMER_ACTOR_TYPES:
        logger.info(
            "zendesk_messaging_webhook_skip_non_user",
            event_id=event_id,
            actor_type=actor_type,
            ticket_id=ticket_id,
            msg_id=msg_id,
        )
        return web.Response(status=200, text="skip non-user actor")

    if not ticket_id:
        logger.warning(
            "zendesk_messaging_webhook_missing_ticket_id",
            event_id=event_id,
            subject=event.get("subject"),
        )
        return web.Response(status=400, text="missing ticket_id")

    dedup = webhook_dedup(request.app)
    event_key = (
        str(event_id) if event_id else f"body:{hashlib.sha256(body).hexdigest()}"
    )
    dedup_key = f"messaging:{event_key}"
    if dedup.seen(dedup_key):
        logger.info(
            "zendesk_messaging_webhook_dup",
            event_id=event_id,
            ticket_id=ticket_id,
            msg_id=msg_id,
        )
        return web.Response(status=200, text="dup")
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
    message = with_trigger_delay(
        {
            "source": "messaging_webhook",
            "ticket_id": ticket_id,
            "conversation_id": conversation_id,
            "messaging_app_id": messaging_app_id,
            "event": event,
            "_queue_key": dedup_key,
        },
        delay_seconds=cfg.public_reply.silence_window_seconds,
        now_fn=time.time,
    )
    try:
        await queue.put(message)
    except Exception as error:
        logger.error(
            "zendesk_messaging_webhook_enqueue_failed",
            event_id=event_id,
            ticket_id=ticket_id,
            error_type=type(error).__name__,
        )
        return web.Response(status=503, text="queue unavailable")
    try:
        manager = session_manager(request.app)
        mark_dirty = getattr(manager, "mark_customer_context_dirty", None)
        if callable(mark_dirty):
            mark_dirty(
                int(ticket_id),
                source="messaging_webhook",
                event_id=str(event_id or msg_id or ""),
            )
    except (KeyError, TypeError, ValueError):
        pass
    except Exception as error:
        logger.warning(
            "zendesk_messaging_context_signal_failed",
            event_id=event_id,
            ticket_id=ticket_id,
            error_type=type(error).__name__,
        )
    dedup.add(dedup_key)
    logger.info(
        "zendesk_messaging_webhook_enqueued",
        event_id=event_id,
        ticket_id=ticket_id,
        msg_id=msg_id,
        has_conversation_id=bool(conversation_id),
        has_messaging_app_id=bool(messaging_app_id),
    )
    return web.Response(status=200, text="ok")
