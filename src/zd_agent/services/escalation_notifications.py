"""Best-effort escalation notification helpers."""
from __future__ import annotations

from zd_agent import config as config_module
from zd_agent.integrations.notification_service import push_notification_card
from zd_agent.integrations.zendesk_mcp import call_zendesk_tool
from zd_agent.log import get_logger
from zd_agent.prompts.public_reply.templates import handoff_template

logger = get_logger(module="services.escalation_notifications")


async def send_handoff_reply_best_effort(
    *,
    ticket_id: int,
    reason: str,
    customer_name: str = "",
    delivery_attempt_id: str = "",
) -> bool:
    """Send the customer-facing handoff reply unless a human already took over."""
    if reason == "human_takeover_detected":
        logger.info(
            "escalated_handoff_reply_skipped_human_takeover",
            ticket_id=ticket_id,
            reason=reason,
        )
        return False

    try:
        from zd_agent.tools.zendesk_public_reply import send_public_reply

        handoff_body = handoff_template(
            customer_name=customer_name,
            reason=reason,
        )
        success, msg = await send_public_reply(
            ticket_id=ticket_id,
            body=handoff_body,
            reply_type="handoff",
            delivery_attempt_id=delivery_attempt_id,
            internal_analysis=(
                f"Escalation reason: {reason}. A customer-safe explanation was "
                "sent before the internal escalation."
            ),
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "escalated_handoff_reply_exception",
            ticket_id=ticket_id,
            error=str(e)[:200],
            exc_info=True,
        )
        return False

    if success:
        logger.info(
            "escalated_handoff_reply_sent",
            ticket_id=ticket_id,
            reason=reason,
        )
        return True

    logger.warning(
        "escalated_handoff_reply_failed",
        ticket_id=ticket_id,
        error=msg[:200],
    )
    return False


async def send_feishu_escalation_best_effort(
    *,
    ticket_id: int,
    reason: str,
    source: str,
    customer_phrase: str = "",
    agent_last_reply: str = "",
    handoff_reply_sent: bool | None = None,
) -> bool:
    """Push the internal escalation card through notification-service MCP."""
    if reason == "human_takeover_detected":
        logger.info(
            "escalated_feishu_skipped_human_takeover",
            ticket_id=ticket_id,
            reason=reason,
        )
        return False

    try:
        cfg = config_module.get_config()
        zendesk_url = (
            f"https://{cfg.zendesk.subdomain}.zendesk.com"
            f"/agent/tickets/{ticket_id}"
            if cfg.zendesk.subdomain else ""
        )
        reason_lines = [f"**升级原因**: {reason}"]
        if customer_phrase:
            reason_lines.append(f"**客户原话**: {customer_phrase[:200]}")
        if agent_last_reply:
            reason_lines.append(f"**Agent 上次回复**: {agent_last_reply[:200]}")
        if handoff_reply_sent is False:
            reason_lines.append(
                "**投递告警**: 客户未收到转人工通知，请立即人工回复。"
            )
        reason_lines.append(f"**source**: {source}")

        payload = await push_notification_card(
            card_type="ticket_escalate",
            ticket_id=str(ticket_id),
            content="\n".join(reason_lines),
            zendesk_url=zendesk_url,
            cfg=cfg,
        )
        if not payload.get("success"):
            raise RuntimeError(f"{payload.get('code')}: {payload.get('error')}")
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "escalated_feishu_failed",
            ticket_id=ticket_id,
            error=str(e)[:200],
            exc_info=True,
        )
        return False

    logger.info(
        "escalated_feishu_pushed",
        ticket_id=ticket_id,
        reason=reason,
    )
    return True


async def add_zendesk_escalation_tag_best_effort(*, ticket_id: int) -> bool:
    """Add the Zendesk escalation tag without blocking other handoff effects."""
    try:
        payload = await call_zendesk_tool(
            "update_ticket_metadata",
            {
                "ticket_id": ticket_id,
                "add_tags": ["escalated-to-human"],
                "explicit_user_metadata_request": "agent escalation tag",
                "dry_run": False,
                "confirm": True,
                "actor": "zd-agent",
            },
        )
        if not payload.get("success"):
            raise RuntimeError(f"{payload.get('code')}: {payload.get('error')}")
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "escalated_zendesk_tag_failed",
            ticket_id=ticket_id,
            error=str(e)[:200],
            exc_info=True,
        )
        return False

    logger.info("escalated_zendesk_tag_added", ticket_id=ticket_id)
    return True
