"""Best-effort side effects for silence-window expiry handling."""
from __future__ import annotations

from zd_agent import config as config_module
from zd_agent.integrations.notification_service import push_notification_card
from zd_agent.integrations.zendesk_mcp import call_zendesk_tool
from zd_agent.log import get_logger
from zd_agent.services.ticket_tags import AGENT_ASKED_TAG

logger = get_logger(module="services.silence_notifications")


async def send_public_nudge_best_effort(
    *,
    ticket_id: int,
    body: str,
    lang: str,
    snapshot_customer_message_count: int,
    snapshot_customer_last_message_id: str | None,
    delivery_attempt_id: str = "",
) -> bool:
    """Send the public nudge and preserve poller visibility with a tag."""
    try:
        from zd_agent.tools.zendesk_public_reply import send_public_reply

        success, message = await send_public_reply(
            ticket_id=ticket_id,
            body=body,
            reply_type="ask",
            snapshot_customer_message_count=snapshot_customer_message_count,
            snapshot_customer_last_message_id=snapshot_customer_last_message_id,
            delivery_attempt_id=delivery_attempt_id,
        )
        if not success:
            raise RuntimeError(message)

        tag_payload = await call_zendesk_tool(
            "update_ticket_metadata",
            {
                "ticket_id": ticket_id,
                "add_tags": [AGENT_ASKED_TAG],
                "explicit_user_metadata_request": "agent asked customer tag",
                "dry_run": False,
                "confirm": True,
                "actor": "zd-agent",
            },
        )
        if not tag_payload.get("success"):
            logger.warning(
                "agent_asked_tag_add_failed",
                ticket_id=ticket_id,
                error=tag_payload.get("error"),
                code=tag_payload.get("code"),
            )
    except Exception as e:  # noqa: BLE001
        logger.error(
            "public_nudge_send_failed",
            ticket_id=ticket_id,
            error=str(e),
        )
        return False

    logger.info(
        "public_nudge_sent",
        ticket_id=ticket_id,
        lang=lang,
        body_chars=len(body),
        tagged=True,
    )
    return True


async def send_silence_expired_alert_best_effort(
    *,
    ticket_id: int,
    verdict: str,
) -> bool:
    """Send the internal alert used when public nudge is disabled."""
    try:
        cfg = config_module.get_config()
        payload = await push_notification_card(
            card_type="alert",
            ticket_id=str(ticket_id),
            title=f"Ticket #{ticket_id} 信息不足等待处理",
            content=(
                f"工单 #{ticket_id} 静默期已结束，但客户消息判为 "
                f"`{verdict or 'unknown'}`，"
                "未达到处理标准。enable_public_nudge=false，"
                "未给客户发 ask 消息。请人工介入或确认是否打开 nudge。"
            ),
            urgent=False,
            cfg=cfg,
        )
        if not payload.get("success"):
            raise RuntimeError(f"{payload.get('code')}: {payload.get('error')}")
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "silence_expired_ask_feishu_failed",
            ticket_id=ticket_id,
            error=str(e),
        )
        return False

    logger.info(
        "silence_expired_ask_feishu_alert",
        ticket_id=ticket_id,
        verdict=verdict,
    )
    return True
