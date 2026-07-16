"""Zendesk ticket message fetching and attachment helpers."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from zd_agent.log import get_logger
from zd_agent.services.ticket_image_download import (
    download_image_data_url as _download_image_data_url,
)
from zd_agent.services.ticket_message_parsing import (
    _customer_public_comments,
    _message_created_at_sort_key,
    _messaging_customer_messages,
    extract_form_data_from_msgs as extract_form_data_from_msgs,
)

logger = get_logger(module="services.ticket_messages")

_IMG_TOTAL_BUDGET = 6
_CONVERSATION_PAGE_SIZE = 100
_MAX_CONVERSATION_PAGES = 10
_MAX_CONVERSATION_EVENTS = 1_000

ZendeskToolCaller = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]


class IncompleteConversationLogError(RuntimeError):
    """Zendesk said more conversation events exist but they were not all loaded."""


def _conversation_page_parts(
    data: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    conversation = data.get("conversation_log") or data
    if not isinstance(conversation, dict):
        return [], {}
    nested = conversation.get("conversation_log") or {}
    if not isinstance(nested, dict):
        nested = {}
    raw_events = conversation.get("events")
    if raw_events is None:
        raw_events = nested.get("events") or []
    events = [event for event in raw_events if isinstance(event, dict)]
    meta = conversation.get("meta") or nested.get("meta") or {}
    return events, meta if isinstance(meta, dict) else {}


async def load_complete_ticket_payload(
    ticket_id: int,
    *,
    zendesk_tool_caller: ZendeskToolCaller | None = None,
) -> dict[str, Any]:
    """Load one ticket and its complete bounded Messaging conversation.

    A partial conversation is more dangerous than a temporary failure: it can
    hide a late customer message or a blocking Answer Bot form.  Therefore all
    malformed cursors and safety-cap exhaustion raise a typed error instead of
    returning the pages collected so far.
    """
    if zendesk_tool_caller is None:
        from zd_agent.integrations.zendesk_mcp import call_zendesk_tool

        zendesk_tool_caller = call_zendesk_tool

    payload = await zendesk_tool_caller(
        "get_ticket",
        {
            "ticket_id": ticket_id,
            "include_comments": True,
            "include_conversation_log": True,
            "conversation_page_size": _CONVERSATION_PAGE_SIZE,
            "force_refresh": True,
        },
    )
    if not payload.get("success"):
        raise RuntimeError(f"{payload.get('code')}: {payload.get('error')}")
    raw_data = payload.get("data") or {}
    if not isinstance(raw_data, dict):
        raise RuntimeError("Zendesk MCP get_ticket returned invalid data")

    events, meta = _conversation_page_parts(raw_data)
    if len(events) >= _MAX_CONVERSATION_EVENTS:
        raise IncompleteConversationLogError(
            f"conversation event cap reached ({len(events)})"
        )

    has_more = bool(meta.get("has_more"))
    after_cursor = meta.get("after_cursor")
    seen_cursors: set[str] = set()
    page_count = 1

    while has_more:
        if len(events) >= _MAX_CONVERSATION_EVENTS:
            raise IncompleteConversationLogError(
                f"conversation event cap reached ({len(events)})"
            )
        if page_count >= _MAX_CONVERSATION_PAGES:
            raise IncompleteConversationLogError(
                f"conversation page cap reached ({page_count})"
            )
        cursor = str(after_cursor or "").strip()
        if not cursor:
            raise IncompleteConversationLogError(
                "conversation has_more=true without after_cursor"
            )
        if cursor in seen_cursors:
            raise IncompleteConversationLogError(
                f"conversation after_cursor repeated: {cursor}"
            )
        seen_cursors.add(cursor)

        page_payload = await zendesk_tool_caller(
            "get_ticket_conversation_log",
            {
                "ticket_id": ticket_id,
                "conversation_after": cursor,
                "conversation_page_size": _CONVERSATION_PAGE_SIZE,
                "force_refresh": True,
            },
        )
        if not page_payload.get("success"):
            raise RuntimeError(
                f"{page_payload.get('code')}: {page_payload.get('error')}"
            )
        page_data = page_payload.get("data") or {}
        if not isinstance(page_data, dict):
            raise RuntimeError(
                "Zendesk MCP get_ticket_conversation_log returned invalid data"
            )
        page_events, page_meta = _conversation_page_parts(page_data)
        events.extend(page_events)
        if len(events) >= _MAX_CONVERSATION_EVENTS:
            raise IncompleteConversationLogError(
                f"conversation event cap reached ({len(events)})"
            )
        page_count += 1
        has_more = bool(page_meta.get("has_more"))
        after_cursor = page_meta.get("after_cursor")
        if page_count >= _MAX_CONVERSATION_PAGES:
            raise IncompleteConversationLogError(
                f"conversation page cap reached ({page_count})"
            )

    data = dict(raw_data)
    conversation = raw_data.get("conversation_log") or {}
    normalized_conversation = (
        dict(conversation) if isinstance(conversation, dict) else {}
    )
    normalized_conversation["events"] = events
    normalized_conversation["meta"] = {
        **meta,
        "has_more": False,
        "after_cursor": after_cursor,
    }
    normalized_conversation.pop("conversation_log", None)
    data["conversation_log"] = normalized_conversation
    return data


async def _collect_recent_image_data_urls(
    customer_msgs: list[dict],
    *,
    lookback: int = 5,
    budget: int = _IMG_TOTAL_BUDGET,
) -> list[str]:
    """从最后 ``lookback`` 条客户消息里抽图片，下载并编码为 data URL。"""
    import asyncio as _asyncio

    recent = customer_msgs[-lookback:] if customer_msgs else []
    urls: list[str] = []
    for m in recent:
        for u in m.get("image_urls") or []:
            urls.append(u)
            if len(urls) >= budget:
                break
        if len(urls) >= budget:
            break

    if not urls:
        return []

    results = await _asyncio.gather(
        *[_download_image_data_url(u) for u in urls],
        return_exceptions=False,
    )
    return [r for r in results if r]


async def _fetch_customer_messages(
    ticket_id: int,
    *,
    log_success: bool = True,
) -> tuple[list[dict], dict | None]:
    """拉工单 + 客户消息（含 messaging 渠道）。"""
    try:
        data = await load_complete_ticket_payload(ticket_id)
        t = data.get("ticket", data)
        c = data.get("comments", [])
        events, _ = _conversation_page_parts(data)
    except Exception as e:
        logger.error(
            "trigger_fetch_ticket_failed",
            ticket_id=ticket_id,
            error=str(e),
            error_type=type(e).__name__,
        )
        return [], None

    ticket, comments = dict(t), c
    ticket["_zd_agent_conversation_events"] = events
    msgs = _customer_public_comments(ticket, comments)
    if events:
        msgs.extend(_messaging_customer_messages(events))
    msgs.sort(key=_message_created_at_sort_key)
    if log_success:
        logger.info(
            "trigger_fetch_messages_ok",
            ticket_id=ticket_id,
            comments_count=len(comments),
            events_count=len(events),
            customer_msgs_count=len(msgs),
            via=(ticket.get("via") or {}).get("channel"),
        )
    return msgs, ticket


async def fetch_customer_messages_for_freshness(ticket_id: int) -> list[dict]:
    messages, ticket = await _fetch_customer_messages(ticket_id)
    if ticket is None:
        raise RuntimeError(f"ticket fetch failed for #{ticket_id}")
    return messages
