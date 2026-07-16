from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterator
from contextlib import suppress
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

from zd_agent.config import AppConfig
from zd_agent.integrations.zendesk_browser_relay import (
    BrowserRelayDefiniteUnsentError,
)
from zd_agent.log import get_logger
from zd_agent.services.public_reply_delivery_store import PublicReplyDeliveryStore
from zd_agent.services.ticket_messages import (
    _conversation_page_parts,
)

logger = get_logger(module="tools.public_reply_delivery")


class PublicReplyTransportError(RuntimeError):
    pass


def _is_loopback_url(url: str) -> bool:
    """Return whether a relay URL is guaranteed to share this host."""
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return False
    return host in {"127.0.0.1", "::1", "localhost"}


def _freshness_callback_url(cfg: AppConfig) -> str:
    """Resolve a callback URL that a relay can safely reach before Send.

    A remote relay cannot call the agent through its own loopback interface.
    Do not silently fall back to localhost: that would either fail delivery or
    remove the final freshness guard entirely.
    """
    configured = str(
        getattr(cfg.public_reply, "freshness_callback_url", "") or ""
    ).strip().rstrip("/")
    relay_url = str(getattr(cfg.public_reply, "browser_relay_url", "") or "")
    if _is_loopback_url(relay_url):
        return configured or (
            f"http://127.0.0.1:{cfg.webhook.listen_port}"
            "/internal/pre-send-freshness"
        )

    try:
        parsed = urlparse(configured)
        callback_host = (parsed.hostname or "").lower()
    except ValueError as exc:
        raise PublicReplyTransportError(
            "Remote browser relay requires an HTTPS non-loopback freshness "
            "callback URL"
        ) from exc
    if (
        parsed.scheme != "https"
        or not callback_host
        or callback_host in {"127.0.0.1", "::1", "localhost"}
        or parsed.query
        or parsed.fragment
    ):
        raise PublicReplyTransportError(
            "Remote browser relay requires an HTTPS non-loopback freshness "
            "callback URL"
        )
    return configured


_DUPLICATE_CHECK_PAGE_SIZE = 100
_DUPLICATE_CHECK_MAX_PAGES = 10
_DUPLICATE_CHECK_MAX_EVENTS = 1_000


async def _renew_delivery_claim(
    store: PublicReplyDeliveryStore,
    delivery_key: str,
    claim_token: str,
) -> None:
    interval = max(0.1, min(30.0, store.claim_lease_seconds / 3))
    while True:
        await asyncio.sleep(interval)
        if not store.renew_claim(delivery_key, claim_token):
            return


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
            "conversation_page_size": _DUPLICATE_CHECK_PAGE_SIZE,
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
    data = payload.get("data")
    if not isinstance(data, dict):
        raise PublicReplyTransportError("Public reply duplicate check returned invalid data")
    # A single conversation page is not enough to prove a previous send did
    # not happen.  Follow every cursor or fail closed before a retry can click
    # Send again.
    events, meta = _conversation_page_parts(data)
    if len(events) >= _DUPLICATE_CHECK_MAX_EVENTS:
        raise PublicReplyTransportError("Public reply duplicate check conversation cap reached")
    has_more = bool(meta.get("has_more"))
    cursor = meta.get("after_cursor")
    seen: set[str] = set()
    page_count = 1
    complete_payload = dict(payload)
    complete_data = dict(data)
    conversation = data.get("conversation_log")
    complete_conversation = dict(conversation) if isinstance(conversation, dict) else {}
    while has_more:
        if page_count >= _DUPLICATE_CHECK_MAX_PAGES:
            raise PublicReplyTransportError("Public reply duplicate check conversation page cap reached")
        cursor_text = str(cursor or "").strip()
        if not cursor_text or cursor_text in seen:
            raise PublicReplyTransportError("Public reply duplicate check conversation cursor incomplete")
        seen.add(cursor_text)
        page = await zendesk_tool_caller(
            "get_ticket_conversation_log",
            {
                "ticket_id": ticket_id,
                "conversation_after": cursor_text,
                "conversation_page_size": _DUPLICATE_CHECK_PAGE_SIZE,
                "force_refresh": True,
            },
        )
        if page.get("success") is not True:
            raise PublicReplyTransportError(
                "Public reply duplicate check conversation page unavailable "
                f"({page.get('code') or 'UNKNOWN'}): {page.get('error') or 'unknown error'}"
            )
        page_data = page.get("data")
        if not isinstance(page_data, dict):
            raise PublicReplyTransportError("Public reply duplicate check conversation page invalid")
        page_events, page_meta = _conversation_page_parts(page_data)
        events.extend(page_events)
        if len(events) >= _DUPLICATE_CHECK_MAX_EVENTS:
            raise PublicReplyTransportError("Public reply duplicate check conversation cap reached")
        page_count += 1
        has_more = bool(page_meta.get("has_more"))
        cursor = page_meta.get("after_cursor")
    complete_conversation["events"] = events
    complete_conversation["meta"] = {"has_more": False}
    complete_data["conversation_log"] = complete_conversation
    complete_payload["data"] = complete_data
    target = _normalize_reply_body(body_with_footer)
    if not target:
        return False
    return any(
        created_at is not None
        and created_at >= not_before - 5
        and _normalize_reply_body(existing) == target
        for existing, created_at in _iter_existing_public_replies(complete_payload)
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
    internal_analysis: str = "",
) -> bool:
    if not conversation_id:
        raise PublicReplyTransportError(
            "Zendesk Messaging conversation_id is missing; "
            "Support API public-comment fallback is disabled"
        )
    delivery_attempt = None
    delivery_claim_token: str | None = None
    delivery_claim_heartbeat: asyncio.Task[None] | None = None
    preserve_delivery_claim = False
    if delivery_attempt_id:
        delivery_store = delivery_store or delivery_store_factory(cfg)
        delivery_attempt = delivery_store.prepare(
            delivery_attempt_id=delivery_attempt_id,
            ticket_id=ticket_id,
            reply_type=reply_type,
            body=body_with_footer,
            internal_analysis=internal_analysis,
            snapshot_customer_message_count=snapshot_customer_message_count,
            snapshot_customer_last_message_id=snapshot_customer_last_message_id,
        )
        body_with_footer = delivery_attempt.body
        reply_type = delivery_attempt.reply_type
        snapshot_customer_message_count = (
            delivery_attempt.snapshot_customer_message_count
        )
        snapshot_customer_last_message_id = (
            delivery_attempt.snapshot_customer_last_message_id or None
        )
        if delivery_attempt.delivered:
            logger.info(
                "public_reply_delivery_already_confirmed",
                ticket_id=ticket_id,
                reply_type=reply_type,
                delivery_key=delivery_attempt.delivery_key[:16],
            )
            return False
        if delivery_attempt.ambiguous:
            if await recent_duplicate_public_reply_exists(
                ticket_id=ticket_id,
                body_with_footer=body_with_footer,
                not_before=delivery_attempt.created_at,
                zendesk_tool_caller=zendesk_tool_caller,
            ):
                delivery_store.confirm_ambiguous_delivered(
                    delivery_attempt.delivery_key,
                )
                return False
            raise PublicReplyTransportError(
                "Public reply delivery is ambiguous and requires manual reconciliation; "
                "automatic resend is blocked"
            )
        delivery_claim_token = delivery_store.claim(delivery_attempt.delivery_key)
        if delivery_claim_token is None:
            refreshed = delivery_store.prepare(
                delivery_attempt_id=delivery_attempt_id,
                ticket_id=ticket_id,
                reply_type=reply_type,
                body=body_with_footer,
                internal_analysis=internal_analysis,
                snapshot_customer_message_count=(
                    snapshot_customer_message_count
                ),
                snapshot_customer_last_message_id=(
                    snapshot_customer_last_message_id
                ),
            )
            if refreshed.delivered:
                return False
            raise PublicReplyTransportError(
                "Public reply delivery attempt is already in progress"
            )
        delivery_claim_heartbeat = asyncio.create_task(
            _renew_delivery_claim(
                delivery_store,
                delivery_attempt.delivery_key,
                delivery_claim_token,
            )
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
            reply_type in {"ask", "solution", "closed"}
            and snapshot_customer_message_count is not None
        )
        # Handoff does not need a stale-answer comparison, but it still must
        # let the relay check for a newly-issued Answer Bot blocking form just
        # before clicking Send.  The callback supports a form-only read when
        # no customer snapshot is available.
        relay_readiness_protected = freshness_protected or reply_type == "handoff"
        freshness_check_url = (
            _freshness_callback_url(cfg) if relay_readiness_protected else None
        )
        try:
            # Persist the ambiguity fence before relay can click Send.  If the
            # process dies after this point, recovery may reconcile but never
            # automatically sends a second customer-visible message.
            if (
                delivery_attempt is not None
                and delivery_store is not None
                and delivery_claim_token is not None
                and not delivery_store.begin_send(
                    delivery_attempt.delivery_key, delivery_claim_token,
                )
            ):
                raise PublicReplyTransportError(
                    "Public reply delivery fence could not be persisted before relay"
                )
            preserve_delivery_claim = True
            await relay_sender(
                url=cfg.public_reply.browser_relay_url,
                api_key=cfg.public_reply.browser_relay_api_key,
                ticket_id=ticket_id,
                body=body_with_footer,
                timeout_seconds=cfg.public_reply.browser_relay_timeout_seconds,
                freshness_check_url=freshness_check_url,
                freshness_check_api_key=(
                    cfg.public_reply.browser_relay_api_key
                    if freshness_check_url else None
                ),
                snapshot_customer_message_count=(
                    snapshot_customer_message_count
                    if relay_readiness_protected else None
                ),
                snapshot_customer_last_message_id=(
                    snapshot_customer_last_message_id
                    if relay_readiness_protected else None
                ),
            )
        except BrowserRelayDefiniteUnsentError:
            preserve_delivery_claim = False
            if (
                delivery_attempt is not None
                and delivery_store is not None
                and delivery_claim_token is not None
                and delivery_store.discard_definitely_unsent(
                    delivery_attempt.delivery_key,
                    delivery_claim_token,
                )
            ):
                delivery_claim_token = None
            raise
        except Exception:
            # begin_send has already installed the durable no-resend fence.
            delivery_claim_token = None
            raise
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
        if delivery_claim_heartbeat is not None:
            delivery_claim_heartbeat.cancel()
            with suppress(asyncio.CancelledError):
                await delivery_claim_heartbeat
        if (
            delivery_attempt is not None
            and delivery_store is not None
            and delivery_claim_token is not None
            and not preserve_delivery_claim
        ):
            delivery_store.release_claim(
                delivery_attempt.delivery_key,
                delivery_claim_token,
            )
