"""Zendesk webhook: signature verification + aiohttp handler."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from datetime import datetime

from aiohttp import web

from zd_agent.log import get_logger
from zd_agent.services.app_context import (
    app_config,
    trigger_queue,
    webhook_dedup,
)
from zd_agent.services.trigger_delay import with_trigger_delay

logger = get_logger(module="services.zendesk_webhook")
SIGNATURE_MAX_AGE_SECONDS = 300
MENTION_CANDIDATE_EVENT = "agent_mention_candidate"


def _signature_timestamp_seconds(timestamp: str) -> float:
    try:
        return float(int(timestamp))
    except ValueError:
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("signature timestamp must include a timezone")
        return parsed.timestamp()


def verify_zendesk_signature(
    *,
    secret: str,
    timestamp: str,
    body: bytes,
    signature: str,
    max_age_seconds: int = SIGNATURE_MAX_AGE_SECONDS,
    now: float | None = None,
) -> bool:
    """Verify HMAC-SHA256 signature per Zendesk webhook spec."""
    if not secret or not timestamp or signature is None:
        return False
    try:
        timestamp_seconds = _signature_timestamp_seconds(timestamp)
    except (OverflowError, ValueError):
        return False
    current_time = time.time() if now is None else now
    if abs(current_time - timestamp_seconds) > max_age_seconds:
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

    if not verify_zendesk_signature(
        secret=secret, timestamp=ts, body=body, signature=sig
    ):
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
    is_mention_candidate = event.get("event") == MENTION_CANDIDATE_EVENT
    if is_mention_candidate and not comment_id:
        return web.Response(status=400, text="missing latest_comment_id")

    body_hash = hashlib.sha256(body).hexdigest()
    key = f"{ticket_id}:{body_hash}"
    dedup = webhook_dedup(request.app)
    if dedup.seen(key):
        logger.info("zendesk_webhook_dup", key=key, comment_id=comment_id)
        return web.Response(status=200, text="dup")
    queue = trigger_queue(request.app)
    if is_mention_candidate:
        normalized_comment_id = str(comment_id)
        message = {
            "source": "mention_candidate",
            "ticket_id": ticket_id,
            "comment_id": normalized_comment_id,
            "_queue_key": f"mention:{normalized_comment_id}",
        }
    else:
        message = with_trigger_delay(
            {
                "source": "zendesk_webhook",
                "ticket_id": ticket_id,
                "event": event,
                "_queue_key": f"zendesk:{key}",
            },
            delay_seconds=cfg.public_reply.silent_initial_wait_seconds,
            now_fn=time.time,
        )
    try:
        await queue.put(message)
    except Exception as error:
        logger.error(
            "zendesk_webhook_enqueue_failed",
            ticket_id=ticket_id,
            error_type=type(error).__name__,
        )
        return web.Response(status=503, text="queue unavailable")
    # The classic ticket webhook has no trustworthy actor type. It fires for
    # Dify bot, internal, and human-agent updates as well as customer updates,
    # so it must not mark an active run's customer snapshot dirty. The typed
    # Messaging webhook and customer-message poller own that signal; this
    # queued event remains a durable discovery fallback.
    dedup.add(key)
    return web.Response(status=200, text="ok")
