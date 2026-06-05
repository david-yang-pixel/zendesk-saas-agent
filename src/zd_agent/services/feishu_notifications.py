"""Best-effort Feishu notification helpers for session lifecycle events."""
from __future__ import annotations

from typing import Any

from zd_agent.config import get_config
from zd_agent.integrations.feishu.client import FeishuClient
from zd_agent.log import get_logger

logger = get_logger(module="services.feishu_notifications")


def _has_successful_ticket_done(session: Any) -> bool:
    for item in getattr(session, "tool_call_log", []) or []:
        if item.get("tool") != "feishu_push":
            continue
        args = item.get("args") or {}
        if args.get("card_type") == "ticket_done" and item.get("success"):
            return True
    return False


def _ticket_done_summary(session: Any) -> str:
    summary = (getattr(session, "reply_draft", "") or "").strip()
    if summary:
        return summary[:1800]
    return (
        f"Agent completed ticket #{getattr(session, 'ticket_id', '')} "
        f"and sent a public reply."
    )


async def notify_ticket_done_best_effort(session: Any) -> bool:
    """Push a deterministic ticket_done card after successful public reply.

    The LLM is still allowed to call ``feishu_push`` itself. This fallback only
    runs when no successful ticket_done push is recorded, so a failed or omitted
    tool call does not silently drop the completion notification.
    """
    ticket_id = getattr(session, "ticket_id", None)
    if _has_successful_ticket_done(session):
        logger.info("feishu_ticket_done_skip_already_sent", ticket_id=ticket_id)
        return False

    cfg = get_config().feishu
    if not cfg.webhook_url:
        logger.warning("feishu_ticket_done_skipped_no_webhook", ticket_id=ticket_id)
        return False

    client = FeishuClient(
        webhook_url=cfg.webhook_url,
        webhook_secret=cfg.webhook_secret,
    )
    try:
        await client.send_ticket_done(
            ticket_id=str(ticket_id),
            summary=_ticket_done_summary(session),
            zendesk_url=f"https://langgenius.zendesk.com/agent/tickets/{ticket_id}",
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "feishu_ticket_done_failed",
            ticket_id=ticket_id,
            error=str(e),
        )
        return False

    logger.info("feishu_ticket_done_pushed", ticket_id=ticket_id)
    return True
