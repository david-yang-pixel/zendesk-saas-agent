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


def reopen_retry_exhausted_ownership(
    *,
    ticket_id: int,
    customer_msgs: list[dict[str, Any]],
    state: Any,
) -> bool:
    """Release transient-failure ownership when the customer adds new work."""
    current = state.get(ticket_id)
    if (
        current is None
        or current.state != "owned"
        or current.ownership_reason != "automation_retry_exhausted"
        or not customer_msgs
    ):
        return False
    latest_id = str(customer_msgs[-1].get("id") or "").strip()
    cursor_already_advanced_by_old_handoff = bool(
        latest_id
        and latest_id == str(current.last_customer_comment_id or "")
        and current.last_verdict == "owned_customer_followup_notified"
    )
    if not latest_id or (
        latest_id == str(current.last_customer_comment_id or "")
        and not cursor_already_advanced_by_old_handoff
    ):
        return False

    def _release(record: Any) -> Any:
        if (
            record is None
            or record.state != "owned"
            or record.ownership_reason != "automation_retry_exhausted"
        ):
            raise RuntimeError("retry-exhausted ownership changed during reopen")
        record.state = "processed"
        record.active_session_id = ""
        record.silence_window_end = None
        record.last_agent_finish_ts = None
        record.consecutive_agent_failures = 0
        if cursor_already_advanced_by_old_handoff:
            record.last_customer_comment_id = None
        record.ownership_action = ""
        record.ownership_reason = ""
        record.ownership_effect_reference = ""
        record.last_verdict = "retry_exhausted_customer_followup_reopened"
        return record

    committed = state.update_if_revision(
        ticket_id,
        expected_revision=current.state_revision,
        expected_state="owned",
        mutate=_release,
    )
    if committed is None:
        logger.info(
            "retry_exhausted_customer_followup_lost_generation",
            ticket_id=ticket_id,
            comment_id=latest_id,
        )
        return False
    logger.info(
        "retry_exhausted_customer_followup_reopened",
        ticket_id=ticket_id,
        comment_id=latest_id,
        previous_effect_reference=current.ownership_effect_reference,
    )
    return True


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
