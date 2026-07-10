"""Verdict dispatch orchestration for trigger consumer messages."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Awaitable, Callable

from zd_agent.log import get_logger
from zd_agent.services.trigger_errors import PublicReplyDeliveryError

logger = get_logger(module="services.verdict_dispatch")

EscalateFn = Callable[..., Awaitable[None]]
SendAskFn = Callable[..., Awaitable[bool]]


PublicAskDeliveryError = PublicReplyDeliveryError


async def handle_post_verdict_dispatch(
    *,
    ticket_id: int,
    verdict: Any,
    customer_msgs: list[dict],
    ticket_info: dict,
    latest_cid: Any,
    latest_ts: Any,
    existing: Any,
    state: Any,
    log_path: Path,
    source: str,
    now: Any,
    cfg: Any,
    escalate_fn: EscalateFn,
    send_ask_fn: SendAskFn,
    conversation_id: str | None = None,
    messaging_app_id: str | None = None,
    delivery_attempt_id: str = "",
) -> None:
    """Dispatch a pre-check verdict into state transitions and side effects."""
    from zd_agent.services.trigger_state_machine import (
        apply_verdict_enter_silence,
        apply_verdict_processed,
        apply_verdict_waiting_for_issue,
    )
    from zd_agent.trigger.decision_log import log_decision

    verdict_str = verdict.verdict
    ask_count_now = existing.ask_count if existing else 0
    max_asks = cfg.public_reply.max_ask_count

    if verdict_str == "request_human":
        last_msg_body = _latest_body(customer_msgs)
        await escalate_fn(
            ticket_id=ticket_id,
            reason="customer_requested",
            existing=existing,
            state=state,
            log_path=log_path,
            source=source,
            now=now,
            verdict_str=verdict_str,
            latest_cid=latest_cid,
            latest_ts=latest_ts,
            completeness=verdict.completeness,
            customer_msgs=customer_msgs,
            customer_phrase=last_msg_body,
            extra_log={"customer_phrase": (verdict.reason or "")[:120]},
        )
        return

    if verdict_str == "self_resolved":
        apply_verdict_processed(
            state,
            ticket_id,
            existing=existing,
            latest_cid=latest_cid,
            latest_ts=latest_ts,
            now=now,
            verdict_str=verdict_str,
            ask_count_now=ask_count_now,
        )
        log_decision(
            log_path=log_path,
            ticket_id=ticket_id,
            verdict=verdict_str,
            completeness=verdict.completeness,
            action="self_resolved",
            extra={"source": source},
        )
        logger.info("trigger_self_resolved", ticket_id=ticket_id, source=source)
        return

    auto_escalate_risks = {"emotional"}
    if (
        verdict_str == "substantial_issue"
        and verdict.high_risk_category in auto_escalate_risks
    ):
        await escalate_fn(
            ticket_id=ticket_id,
            reason=f"high_risk_{verdict.high_risk_category}",
            existing=existing,
            state=state,
            log_path=log_path,
            source=source,
            now=now,
            verdict_str=verdict_str,
            latest_cid=latest_cid,
            latest_ts=latest_ts,
            completeness=verdict.completeness,
            customer_msgs=customer_msgs,
            customer_phrase=_latest_body(customer_msgs),
            extra_log={
                "high_risk_category": verdict.high_risk_category,
                "high_risk_reason": (verdict.high_risk_reason or "")[:120],
            },
        )
        return

    if (
        verdict_str == "substantial_issue"
        and verdict.high_risk_category in ("refund", "incident")
    ):
        logger.info(
            "verdict_high_risk_hint_not_auto_escalating",
            ticket_id=ticket_id,
            high_risk_category=verdict.high_risk_category,
            high_risk_reason=(verdict.high_risk_reason or "")[:120],
            note=(
                "refund: agent 查 DB/政策后 public_reply + 飞书推财务；"
                "incident: agent 用 SigNoz 验证再决定"
            ),
        )

    if verdict_str == "substantial_issue":
        silence_min = cfg.trigger_decider.silence_minutes_default
        transition = apply_verdict_enter_silence(
            state,
            ticket_id,
            existing=existing,
            latest_cid=latest_cid,
            latest_ts=latest_ts,
            now=now,
            verdict_str=verdict_str,
            ask_count_now=ask_count_now,
            silence_minutes=silence_min,
        )
        log_decision(
            log_path=log_path,
            ticket_id=ticket_id,
            verdict=verdict_str,
            completeness=verdict.completeness,
            silence_min=silence_min,
            action="enter_silence",
            extra={"source": source},
        )
        logger.info(
            "trigger_enter_silence",
            ticket_id=ticket_id,
            silence_min=silence_min,
            window_end=transition.silence_window_end.isoformat(),
            source=source,
        )
        return

    if ask_count_now >= max_asks:
        await escalate_fn(
            ticket_id=ticket_id,
            reason="max_asks_reached",
            existing=existing,
            state=state,
            log_path=log_path,
            source=source,
            now=now,
            verdict_str=verdict_str,
            latest_cid=latest_cid,
            latest_ts=latest_ts,
            completeness=verdict.completeness,
            customer_msgs=customer_msgs,
            customer_phrase=_latest_body(customer_msgs),
            extra_log={"ask_count": ask_count_now, "max_ask_count": max_asks},
        )
        return

    sent = await send_ask_fn(
        ticket_id=ticket_id,
        verdict_kind=verdict_str,
        customer_msgs=customer_msgs,
        ticket_info=ticket_info,
        ask_count=ask_count_now,
        source=source,
        conversation_id=conversation_id,
        messaging_app_id=messaging_app_id,
        delivery_attempt_id=delivery_attempt_id,
    )

    if not sent:
        log_decision(
            log_path=log_path,
            ticket_id=ticket_id,
            verdict=verdict_str,
            completeness=verdict.completeness,
            action="ask_customer_failed",
            extra={
                "source": source,
                "ask_count": ask_count_now,
            },
        )
        logger.warning(
            "trigger_ask_customer_failed",
            ticket_id=ticket_id,
            verdict=verdict_str,
            ask_count=ask_count_now,
            source=source,
        )
        raise PublicReplyDeliveryError(ticket_id, kind="ask")

    transition = apply_verdict_waiting_for_issue(
        state,
        ticket_id,
        existing=existing,
        latest_cid=latest_cid,
        latest_ts=latest_ts,
        now=now,
        verdict_str=verdict_str,
        ask_count_now=ask_count_now,
        sent=sent,
    )
    log_decision(
        log_path=log_path,
        ticket_id=ticket_id,
        verdict=verdict_str,
        completeness=verdict.completeness,
        action="ask_customer",
        extra={
            "source": source,
            "ask_count_before": ask_count_now,
            "ask_count_after": transition.ask_count_after,
            "sent": sent,
        },
    )
    logger.info(
        "trigger_ask_customer",
        ticket_id=ticket_id,
        verdict=verdict_str,
        ask_count_before=ask_count_now,
        ask_count_after=transition.ask_count_after,
        source=source,
        sent=sent,
    )


def _latest_body(customer_msgs: list[dict]) -> str:
    if not customer_msgs:
        return ""
    return (customer_msgs[-1].get("body") or "")[:200]
