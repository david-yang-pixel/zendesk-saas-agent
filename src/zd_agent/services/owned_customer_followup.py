"""Customer follow-up handling after a ticket has a human owner."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from zd_agent.integrations.notification_service import push_notification_card
from zd_agent.log import get_logger
from zd_agent.services.notification_outbox import (
    NotificationOutbox,
    reconcile_notifications,
)

logger = get_logger(module="services.owned_customer_followup")

_OWNED_STATES = frozenset({"owned", "externally_owned", "escalated_to_human"})
PushNotification = Callable[..., Awaitable[dict[str, Any]]]


async def notify_owned_customer_followup(
    *,
    ticket_id: int,
    customer_msgs: list[dict[str, Any]],
    state: Any,
    state_dir: Path,
    push: PushNotification = push_notification_card,
) -> bool:
    """Notify the human owner of a new customer message without resuming AI."""
    current = state.get(ticket_id)
    if current is None or current.state not in _OWNED_STATES:
        return False
    if not customer_msgs:
        return True
    latest = customer_msgs[-1]
    latest_id = str(latest.get("id") or "").strip()
    if not latest_id or latest_id == str(current.last_customer_comment_id or ""):
        return True

    body = str(latest.get("body") or "").strip()
    content = "\n".join(
        (
            "人工接管中的工单收到客户新消息，请继续处理。",
            f"当前责任：{current.ownership_action or current.state}",
            f"接管原因：{current.ownership_reason or current.escalated_reason or '未记录'}",
            "",
            "客户最新消息：",
            body[:4000] or "（仅包含附件或结构化内容，请在 Zendesk 查看。）",
        )
    )
    store = NotificationOutbox(state_dir / "notification_outbox.sqlite3")
    store.prepare(
        reference=f"owned-customer-followup:{ticket_id}:{latest_id}",
        ticket_id=ticket_id,
        card_type="ticket_escalate",
        title="人工接管工单有客户新消息",
        content=content,
        zendesk_url=f"https://langgenius.zendesk.com/agent/tickets/{ticket_id}",
        urgent=True,
    )

    def _advance_cursor(record: Any) -> Any:
        if record is None or record.state not in _OWNED_STATES:
            raise RuntimeError("ticket ownership changed during follow-up")
        record.last_customer_comment_id = latest_id
        record.last_verdict = "owned_customer_followup_notified"
        return record

    committed = state.update_if_revision(
        ticket_id,
        expected_revision=current.state_revision,
        expected_state=current.state,
        mutate=_advance_cursor,
    )
    if committed is None:
        logger.info(
            "owned_customer_followup_lost_generation",
            ticket_id=ticket_id,
            comment_id=latest_id,
        )
        return True
    await reconcile_notifications(store=store, push=push, limit=20)
    logger.info(
        "owned_customer_followup_queued",
        ticket_id=ticket_id,
        comment_id=latest_id,
        ownership_state=current.state,
    )
    return True
