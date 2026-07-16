"""Verdict dispatch orchestration for trigger consumer messages."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Awaitable, Callable

from zd_agent.log import get_logger
from zd_agent.services.trigger_errors import PublicReplyDeliveryError

logger = get_logger(module="services.verdict_dispatch")

EscalateFn = Callable[..., Awaitable[None]]
SendAskFn = Callable[..., Awaitable[bool]]
SendClosedFn = Callable[..., Awaitable[bool]]


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
    send_closed_fn: SendClosedFn | None = None,
    conversation_id: str | None = None,
    messaging_app_id: str | None = None,
    delivery_attempt_id: str = "",
    expected_state_revision: int | None = None,
    expected_active_session_id: str | None = None,
) -> None:
    """Dispatch a pre-check verdict into state transitions and side effects."""
    from zd_agent.services.trigger_state_machine import (
        apply_verdict_enter_silence,
        apply_verdict_processed,
        apply_verdict_waiting_for_issue,
    )
    from zd_agent.trigger.decision_log import log_decision

    verdict_str = verdict.verdict
    # The trigger handler may have claimed a short-lived operation owner while
    # the classifier and public-reply relay were awaited.  State transitions
    # after those side effects must still belong to that exact generation.
    transition_state: Any = state
    if expected_state_revision is not None and expected_active_session_id is not None:
        transition_state = _ClaimedTransitionState(
            state,
            ticket_id=ticket_id,
            expected_revision=expected_state_revision,
            expected_owner=expected_active_session_id,
        )
    ask_count_now = existing.ask_count if existing else 0
    max_asks = cfg.public_reply.max_ask_count

    if verdict_str == "request_human":
        last_msg_body = _latest_body(customer_msgs)
        await escalate_fn(
            ticket_id=ticket_id,
            reason="customer_requested",
            existing=existing,
            state=transition_state,
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
        if send_closed_fn is None:
            raise RuntimeError("send_closed_fn is required for self_resolved")
        if isinstance(transition_state, _ClaimedTransitionState):
            transition_state.require_current()
        sent_result = await send_closed_fn(
            ticket_id=ticket_id,
            customer_msgs=customer_msgs,
            ticket_info=ticket_info,
            source=source,
            conversation_id=conversation_id,
            messaging_app_id=messaging_app_id,
            delivery_attempt_id=delivery_attempt_id,
        )
        if not _delivery_succeeded(sent_result):
            log_decision(
                log_path=log_path,
                ticket_id=ticket_id,
                verdict=verdict_str,
                completeness=verdict.completeness,
                action="closed_reply_failed",
                extra={"source": source},
            )
            raise PublicReplyDeliveryError(
                ticket_id,
                kind="closed",
                ambiguous=_delivery_is_ambiguous(sent_result),
            )
        apply_verdict_processed(
            transition_state,
            ticket_id,
            existing=existing,
            latest_cid=_accepted_customer_cursor(sent_result, latest_cid),
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
            action="closed_reply_sent",
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
            state=transition_state,
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
            transition_state,
            ticket_id,
            existing=existing,
            latest_cid=latest_cid,
            latest_ts=latest_ts,
            now=now,
            verdict_str=verdict_str,
            ask_count_now=ask_count_now,
            silence_minutes=silence_min,
            delivery_attempt_id=delivery_attempt_id,
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
            state=transition_state,
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

    if isinstance(transition_state, _ClaimedTransitionState):
        transition_state.require_current()
    sent_result = await send_ask_fn(
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

    if not _delivery_succeeded(sent_result):
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
        raise PublicReplyDeliveryError(
            ticket_id,
            kind="ask",
            ambiguous=_delivery_is_ambiguous(sent_result),
        )

    sent = True

    transition = apply_verdict_waiting_for_issue(
        transition_state,
        ticket_id,
        existing=existing,
        latest_cid=_accepted_customer_cursor(sent_result, latest_cid),
        latest_ts=latest_ts,
        now=now,
        verdict_str=verdict_str,
        ask_count_now=ask_count_now,
        sent=True,
        delivery_attempt_id=delivery_attempt_id,
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


class _ClaimedTransitionState:
    """Adapter that turns legacy ``state.set`` transitions into owner CASes."""

    def __init__(
        self,
        state: Any,
        *,
        ticket_id: int,
        expected_revision: int,
        expected_owner: str,
    ) -> None:
        self._state = state
        self._ticket_id = ticket_id
        self._expected_revision = expected_revision
        self._expected_owner = expected_owner

    def set(self, ticket_id: str | int, desired: Any) -> None:
        if int(ticket_id) != self._ticket_id:
            raise RuntimeError("verdict transition changed ticket id")
        commit = self._state.update_if_revision(
            ticket_id,
            expected_revision=self._expected_revision,
            expected_state="processing",
            expected_active_session_id=self._expected_owner,
            mutate=lambda _current: desired,
        )
        if commit is None:
            raise RuntimeError("verdict operation lost ownership before finalization")

    def require_current(self) -> None:
        current = self._state.get(self._ticket_id)
        if (
            current is None
            or current.state_revision != self._expected_revision
            or current.state != "processing"
            or current.active_session_id != self._expected_owner
        ):
            raise RuntimeError("verdict operation lost ownership before delivery")


def _delivery_succeeded(result: Any) -> bool:
    """Accept legacy bool test doubles and typed direct-send outcomes."""
    success = getattr(result, "success", result)
    return success is True


def _accepted_customer_cursor(result: Any, fallback: Any) -> Any:
    """Use the harmless follow-up cursor accepted by the final send gate."""
    accepted = getattr(result, "accepted_snapshot_last_id", None)
    return accepted if accepted else fallback


def _delivery_is_ambiguous(result: Any) -> bool:
    message = str(getattr(result, "message", "")).lower()
    return "ambiguous" in message or "already in progress" in message
