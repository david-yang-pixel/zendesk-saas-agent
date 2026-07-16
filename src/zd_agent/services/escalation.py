"""Escalation orchestration for trigger-driven tickets."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from zd_agent.log import get_logger
from zd_agent.services import escalation_notifications
from zd_agent.services.ticket_messages import extract_form_data_from_msgs
from zd_agent.services.trigger_state_machine import apply_escalated_to_human

logger = get_logger(module="services.escalation")


async def set_escalated_to_human(
    *,
    ticket_id: int,
    reason: str,
    existing: Any,
    state: Any,
    log_path: Path,
    source: str,
    now: Any,
    verdict_str: str,
    latest_cid: Any,
    latest_ts: Any,
    completeness: int,
    customer_msgs: list[dict] | None = None,
    customer_phrase: str = "",
    agent_last_reply: str = "",
    extra_log: dict | None = None,
) -> None:
    """Persist escalated state and run best-effort escalation side effects."""
    from zd_agent.trigger.decision_log import log_decision

    apply_escalated_to_human(
        state,
        ticket_id,
        existing=existing,
        latest_cid=latest_cid,
        latest_ts=latest_ts,
        now=now,
        verdict_str=verdict_str,
        reason=reason,
    )

    log_payload = {"source": source, "reason": reason}
    if extra_log:
        log_payload.update(extra_log)
    log_decision(
        log_path=log_path,
        ticket_id=ticket_id,
        verdict=verdict_str,
        completeness=completeness,
        action="escalated_to_human",
        extra=log_payload,
    )
    logger.info(
        "trigger_escalated_to_human",
        ticket_id=ticket_id,
        reason=reason,
        source=source,
        verdict=verdict_str,
    )

    if reason == "customer_requested":
        from zd_agent.observability.metrics import emit_customer_requested_human

        emit_customer_requested_human(
            ticket_id=ticket_id,
            source=source,
            customer_phrase=customer_phrase,
            ask_count_so_far=existing.ask_count if existing else 0,
        )

    form = extract_form_data_from_msgs(customer_msgs or [])
    customer_name = (form.get("name", "") or "").strip()
    handoff_reply_sent = (
        await escalation_notifications.send_handoff_reply_best_effort(
            ticket_id=ticket_id,
            reason=reason,
            customer_name=customer_name,
            delivery_attempt_id=(
                f"{source}:{ticket_id}:{latest_cid or verdict_str}:handoff"
            ),
            source=source,
            customer_phrase=customer_phrase,
        )
    )

    if not handoff_reply_sent:
        # Delivery never reached the durable post-public boundary, so alert the
        # operator immediately and tag the ticket through the fallback path.
        await escalation_notifications.send_feishu_escalation_best_effort(
            ticket_id=ticket_id,
            reason=reason,
            source=source,
            customer_phrase=customer_phrase,
            agent_last_reply=agent_last_reply,
            handoff_reply_sent=False,
        )
        await escalation_notifications.add_zendesk_escalation_tag_best_effort(
            ticket_id=ticket_id,
        )
