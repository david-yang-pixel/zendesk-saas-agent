"""Zendesk ticket message fetching and attachment helpers."""
from __future__ import annotations

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
    from zd_agent.integrations.zendesk_mcp import call_zendesk_tool

    try:
        payload = await call_zendesk_tool(
            "get_ticket",
            {
                "ticket_id": ticket_id,
                "include_comments": True,
                "include_conversation_log": True,
                "conversation_page_size": 100,
                "force_refresh": True,
            },
        )
        if not payload.get("success"):
            raise RuntimeError(f"{payload.get('code')}: {payload.get('error')}")
        data = payload.get("data") or {}
        t = data.get("ticket", data)
        c = data.get("comments", [])
        conversation = data.get("conversation_log") or {}
        events = (
            conversation.get("events")
            or (conversation.get("conversation_log") or {}).get("events")
            or []
        )
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
