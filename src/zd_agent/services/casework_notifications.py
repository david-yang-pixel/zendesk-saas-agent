"""Typed post-delivery notifications for Zendesk Agent v2 ownership."""

from __future__ import annotations

from zd_agent import config as config_module
from zd_agent.casework.domain.enums import OwnershipAction, ReasonCode
from zd_agent.integrations.notification_service import push_notification_card
from zd_agent.integrations.zendesk_mcp import call_zendesk_tool
from zd_agent.log import get_logger


logger = get_logger(module="services.casework_notifications")


def _typed_ownership(
    ownership_action: str,
    reason_code: str,
) -> tuple[OwnershipAction, ReasonCode]:
    action = OwnershipAction(ownership_action)
    reason = ReasonCode(reason_code)
    if action is OwnershipAction.NONE:
        raise ValueError("ownership notification requires a target team")
    return action, reason


def _card_content(
    *,
    action: OwnershipAction,
    reason: ReasonCode,
    ownership_packet: str,
    customer_reply: str,
) -> str:
    sections = [
        "**v2 所有权任务**",
        f"**目标团队**\n{action.value}",
        f"**原因代码**\n{reason.value}",
        f"**可执行处理包**\n{ownership_packet.strip()}",
        "**客户通知状态**\n✅ 客户回复已确认投递",
    ]
    if reply := customer_reply.strip():
        sections.append(f"**已发送给客户的回复**\n{reply}")
    return "\n\n".join(sections)


async def send_casework_ownership_notification_best_effort(
    *,
    ticket_id: int,
    ownership_action: str,
    reason_code: str,
    ownership_packet: str,
    customer_reply: str,
    source: str,
    idempotency_reference: str,
) -> bool:
    """Notify the typed owner only after the customer reply is confirmed."""
    try:
        action, reason = _typed_ownership(ownership_action, reason_code)
        cfg = config_module.get_config()
        zendesk_url = (
            f"https://{cfg.zendesk.subdomain}.zendesk.com/agent/tickets/{ticket_id}"
            if cfg.zendesk.subdomain
            else ""
        )
        payload = await push_notification_card(
            card_type="ticket_escalate",
            ticket_id=str(ticket_id),
            content=_card_content(
                action=action,
                reason=reason,
                ownership_packet=ownership_packet,
                customer_reply=customer_reply,
            ),
            zendesk_url=zendesk_url,
            cfg=cfg,
        )
        if not payload.get("success"):
            raise RuntimeError(f"{payload.get('code')}: {payload.get('error')}")
    except Exception as error:  # noqa: BLE001 - this is a durable best-effort edge.
        logger.warning(
            "casework_ownership_notification_failed",
            ticket_id=ticket_id,
            ownership_action=ownership_action,
            reason_code=reason_code,
            source=source,
            idempotency_reference=idempotency_reference,
            error=str(error)[:200],
            exc_info=True,
        )
        return False
    logger.info(
        "casework_ownership_notification_sent",
        ticket_id=ticket_id,
        ownership_action=ownership_action,
        reason_code=reason_code,
        source=source,
        idempotency_reference=idempotency_reference,
    )
    return True


async def add_casework_ownership_tags_best_effort(
    *,
    ticket_id: int,
    ownership_action: str,
    reason_code: str,
) -> bool:
    """Persist typed v2 ownership metadata on the Zendesk ticket."""
    try:
        action, reason = _typed_ownership(ownership_action, reason_code)
        payload = await call_zendesk_tool(
            "update_ticket_metadata",
            {
                "ticket_id": ticket_id,
                "add_tags": [
                    "casework-v2",
                    f"casework-owner-{action.value}",
                    f"casework-reason-{reason.value}",
                ],
                "explicit_user_metadata_request": "v2 typed ownership tags",
                "dry_run": False,
                "confirm": True,
                "actor": "zd-agent-v2",
            },
        )
        if not payload.get("success"):
            raise RuntimeError(f"{payload.get('code')}: {payload.get('error')}")
    except Exception as error:  # noqa: BLE001 - durable reconciliation retries it.
        logger.warning(
            "casework_ownership_tags_failed",
            ticket_id=ticket_id,
            ownership_action=ownership_action,
            reason_code=reason_code,
            error=str(error)[:200],
            exc_info=True,
        )
        return False
    logger.info(
        "casework_ownership_tags_added",
        ticket_id=ticket_id,
        ownership_action=ownership_action,
        reason_code=reason_code,
    )
    return True
