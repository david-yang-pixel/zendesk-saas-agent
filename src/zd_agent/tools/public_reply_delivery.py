from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterator
from datetime import datetime
from typing import Any

from zd_agent.config import AppConfig
from zd_agent.log import get_logger
from zd_agent.services.public_reply_delivery_store import PublicReplyDeliveryStore

logger = get_logger(module="tools.public_reply_delivery")


class PublicReplyTransportError(RuntimeError):
    pass


def _normalize_reply_body(body: str) -> str:
    normalized = (body or "").replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(normalized.split()).strip()


def _reply_created_at(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _conversation_event_text(event: dict[str, Any]) -> str:
    content = event.get("content")
    if not isinstance(content, dict):
        return ""
    for key in ("text", "body", "html_text"):
        value = content.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def _iter_existing_public_replies(
    payload: dict[str, Any],
) -> Iterator[tuple[str, float | None]]:
    data = payload.get("data")
    if not isinstance(data, dict):
        return
    comments = data.get("comments")
    if isinstance(comments, list):
        for comment in comments:
            if not isinstance(comment, dict) or comment.get("public") is not True:
                continue
            for key in ("body", "plain_body", "html_body"):
                value = comment.get(key)
                if isinstance(value, str) and value.strip():
                    yield value, _reply_created_at(
                        comment.get("created_at") or comment.get("timestamp")
                    )
                    break
    conversation_log = data.get("conversation_log")
    if not isinstance(conversation_log, dict):
        return
    events = conversation_log.get("events")
    if not isinstance(events, list):
        return
    for event in events:
        if not isinstance(event, dict):
            continue
        author = event.get("author")
        if not isinstance(author, dict) or author.get("type") != "agent":
            continue
        text = _conversation_event_text(event)
        if text:
            yield text, _reply_created_at(
                event.get("created_at") or event.get("timestamp")
            )


async def recent_duplicate_public_reply_exists(
    *,
    ticket_id: int,
    body_with_footer: str,
    not_before: float,
    zendesk_tool_caller: Callable[..., Awaitable[dict[str, Any]]],
) -> bool:
    payload = await zendesk_tool_caller(
        "get_ticket_comments",
        {
            "ticket_id": ticket_id,
            "include_conversation_log": True,
            "conversation_page_size": 100,
            "force_refresh": True,
        },
    )
    if payload.get("success") is not True:
        code = str(payload.get("code") or "UNKNOWN")
        error = str(payload.get("error") or "unknown error")
        logger.warning(
            "public_reply_duplicate_check_unavailable",
            ticket_id=ticket_id,
            code=code,
            error=error[:200],
        )
        raise PublicReplyTransportError(
            f"Public reply duplicate check unavailable ({code}): {error}"
        )
    target = _normalize_reply_body(body_with_footer)
    if not target:
        return False
    return any(
        created_at is not None
        and created_at >= not_before - 5
        and _normalize_reply_body(existing) == target
        for existing, created_at in _iter_existing_public_replies(payload)
    )


async def send_public_reply_safely(
    *,
    cfg: AppConfig,
    ticket_id: int,
    body_with_footer: str,
    reply_type: str,
    conversation_id: str | None,
    snapshot_customer_message_count: int | None,
    snapshot_customer_last_message_id: str | None,
    delivery_attempt_id: str | None,
    delivery_store: PublicReplyDeliveryStore | None,
    delivery_store_factory: Callable[[AppConfig], PublicReplyDeliveryStore],
    zendesk_tool_caller: Callable[..., Awaitable[dict[str, Any]]],
    relay_sender: Callable[..., Awaitable[Any]],
) -> bool:
    if not conversation_id:
        raise PublicReplyTransportError(
            "Zendesk Messaging conversation_id is missing; "
            "Support API public-comment fallback is disabled"
        )
    delivery_attempt = None
    delivery_claim_token: str | None = None
    if delivery_attempt_id:
        delivery_store = delivery_store or delivery_store_factory(cfg)
        delivery_attempt = delivery_store.prepare(
            delivery_attempt_id=delivery_attempt_id,
            ticket_id=ticket_id,
            reply_type=reply_type,
            body=body_with_footer,
        )
        body_with_footer = delivery_attempt.body
        if delivery_attempt.delivered:
            logger.info(
                "public_reply_delivery_already_confirmed",
                ticket_id=ticket_id,
                reply_type=reply_type,
                delivery_key=delivery_attempt.delivery_key[:16],
            )
            return False
        delivery_claim_token = delivery_store.claim(delivery_attempt.delivery_key)
        if delivery_claim_token is None:
            refreshed = delivery_store.prepare(
                delivery_attempt_id=delivery_attempt_id,
                ticket_id=ticket_id,
                reply_type=reply_type,
                body=body_with_footer,
            )
            if refreshed.delivered:
                return False
            raise PublicReplyTransportError(
                "Public reply delivery attempt is already in progress"
            )
    try:
        if (
            delivery_attempt is not None
            and delivery_store is not None
            and delivery_claim_token is not None
            and await recent_duplicate_public_reply_exists(
                ticket_id=ticket_id,
                body_with_footer=body_with_footer,
                not_before=delivery_attempt.created_at,
                zendesk_tool_caller=zendesk_tool_caller,
            )
        ):
            if not delivery_store.mark_delivered(
                delivery_attempt.delivery_key,
                delivery_claim_token,
            ):
                raise PublicReplyTransportError(
                    "Public reply delivery claim expired during confirmation"
                )
            delivery_claim_token = None
            logger.info(
                "public_reply_delivery_already_confirmed",
                ticket_id=ticket_id,
                reply_type=reply_type,
                delivery_key=delivery_attempt.delivery_key[:16],
            )
            return False

        freshness_protected = (
            reply_type in {"ask", "solution"}
            and snapshot_customer_message_count is not None
        )
        freshness_check_url = (
            f"http://127.0.0.1:{cfg.webhook.listen_port}/internal/pre-send-freshness"
            if freshness_protected
            else None
        )
        await relay_sender(
            url=cfg.public_reply.browser_relay_url,
            api_key=cfg.public_reply.browser_relay_api_key,
            ticket_id=ticket_id,
            body=body_with_footer,
            timeout_seconds=cfg.public_reply.browser_relay_timeout_seconds,
            freshness_check_url=freshness_check_url,
            freshness_check_api_key=(
                cfg.public_reply.browser_relay_api_key if freshness_check_url else None
            ),
            snapshot_customer_message_count=(
                snapshot_customer_message_count if freshness_protected else None
            ),
            snapshot_customer_last_message_id=(
                snapshot_customer_last_message_id if freshness_protected else None
            ),
        )
        if (
            delivery_attempt is not None
            and delivery_store is not None
            and delivery_claim_token is not None
        ):
            if not delivery_store.mark_delivered(
                delivery_attempt.delivery_key,
                delivery_claim_token,
            ):
                raise PublicReplyTransportError(
                    "Public reply delivery claim expired after relay send"
                )
            delivery_claim_token = None
        return True
    finally:
        if (
            delivery_attempt is not None
            and delivery_store is not None
            and delivery_claim_token is not None
        ):
            delivery_store.release_claim(
                delivery_attempt.delivery_key,
                delivery_claim_token,
            )
