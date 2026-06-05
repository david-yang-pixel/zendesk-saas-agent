"""Best-effort side effects for silence-window expiry handling."""
from __future__ import annotations

import asyncio

from zd_agent import config as config_module
from zd_agent.integrations.feishu import client as feishu_client
from zd_agent.log import get_logger
from zd_agent.services.ticket_tags import AGENT_ASKED_TAG

logger = get_logger(module="services.silence_notifications")


async def send_public_nudge_best_effort(
    *,
    ticket_id: int,
    body: str,
    lang: str,
) -> bool:
    """Send the public nudge and preserve poller visibility with a tag."""
    try:
        from zd.client import ZendeskClient

        def _send_reply_and_tag() -> None:
            client = ZendeskClient()
            client.reply_ticket(ticket_id, body=body, public=True)
            try:
                client.add_ticket_tags(ticket_id, [AGENT_ASKED_TAG])
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "agent_asked_tag_add_failed",
                    ticket_id=ticket_id,
                    error=str(e),
                )

        await asyncio.to_thread(_send_reply_and_tag)
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
        fcfg = config_module.get_config().feishu
        if not fcfg.webhook_url:
            logger.info(
                "silence_expired_ask_feishu_alert",
                ticket_id=ticket_id,
                verdict=verdict,
            )
            return False

        client = feishu_client.FeishuClient(
            webhook_url=fcfg.webhook_url,
            webhook_secret=fcfg.webhook_secret,
        )
        await client.send_alert(
            title=f"Ticket #{ticket_id} 信息不足等待处理",
            message=(
                f"工单 #{ticket_id} 静默期已结束，但客户消息判为 "
                f"`{verdict or 'unknown'}`，"
                "未达到处理标准。enable_public_nudge=false，"
                "未给客户发 ask 消息。请人工介入或确认是否打开 nudge。"
            ),
            urgent=False,
        )
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
