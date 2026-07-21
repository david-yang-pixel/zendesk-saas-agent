"""Best-effort escalation notification helpers."""
from __future__ import annotations

from zd_agent import config as config_module
from zd_agent.integrations.notification_service import push_notification_card
from zd_agent.integrations.zendesk_mcp import call_zendesk_tool
from zd_agent.log import get_logger
from zd_agent.prompts.public_reply.templates import handoff_template

logger = get_logger(module="services.escalation_notifications")

_ESCALATION_REASON_SUMMARIES = {
    "agent_handoff": "Agent 已完成初步处理，后续需要人工继续跟进。",
    "customer_requested": "客户明确要求人工支持。",
    "high_risk_emotional": "客户情绪或潜在风险较高，需要人工优先跟进。",
    "high_risk_refund": "退款或账务操作需要人工审核与执行。",
    "max_asks_reached": "多轮补充信息后仍无法自动完成，需要人工继续处理。",
    "sre_investigation": "需要工程或 SRE 继续调查。",
}


def _escalation_reason_summary(reason: str) -> str:
    normalized = reason.strip()
    if normalized in _ESCALATION_REASON_SUMMARIES:
        return _ESCALATION_REASON_SUMMARIES[normalized]
    if normalized.startswith("high_risk_"):
        return "该工单涉及高风险场景，需要人工审核处理。"
    if normalized.endswith("_retry_exhausted"):
        return "自动处理多次重试仍未成功，需要人工接管。"
    return normalized or "未提供升级原因。"


def _escalation_card_content(
    *,
    reason: str,
    customer_phrase: str,
    agent_last_reply: str,
    handoff_reply_sent: bool | None,
) -> str:
    """Build an actionable card without clipping or internal trace fields."""
    sections = [f"**升级原因**\n{_escalation_reason_summary(reason)}"]

    if handoff_reply_sent is False:
        sections.append(
            "**⚠️ 投递告警**\n客户未收到转人工通知，请立即人工回复。"
        )
    elif handoff_reply_sent is True:
        sections.append("**客户通知状态**\n✅ 转人工说明已成功发送")

    normalized_phrase = customer_phrase.strip()
    if normalized_phrase:
        sections.append(f"**客户原话**\n{normalized_phrase}")

    normalized_reply = agent_last_reply.strip()
    if normalized_reply:
        reply_heading = (
            "已发送给客户的回复"
            if handoff_reply_sent is True
            else "Agent 上次回复（供人工参考）"
        )
        sections.append(f"**{reply_heading}**\n{normalized_reply}")

    return "\n\n".join(sections)


async def send_handoff_reply_best_effort(
    *,
    ticket_id: int,
    reason: str,
    customer_name: str = "",
    delivery_attempt_id: str = "",
    source: str = "trigger_escalation",
    customer_phrase: str = "",
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
            handoff_reason=reason,
            handoff_source=source,
            handoff_customer_phrase=customer_phrase,
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
    idempotency_reference: str = "",
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

        payload = await push_notification_card(
            card_type="ticket_escalate",
            ticket_id=str(ticket_id),
            content=_escalation_card_content(
                reason=reason,
                customer_phrase=customer_phrase,
                agent_last_reply=agent_last_reply,
                handoff_reply_sent=handoff_reply_sent,
            ),
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
            reason=reason,
            source=source,
            handoff_reply_sent=handoff_reply_sent,
            idempotency_reference=idempotency_reference,
            exc_info=True,
        )
        return False

    logger.info(
        "escalated_feishu_pushed",
        ticket_id=ticket_id,
        reason=reason,
        source=source,
        handoff_reply_sent=handoff_reply_sent,
        idempotency_reference=idempotency_reference,
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
