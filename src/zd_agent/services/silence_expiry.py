"""Silence-window expiry orchestration for insufficient customer messages."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from zd_agent.config import get_config
from zd_agent.log import get_logger
from zd_agent.trigger.decision_log import log_decision

logger = get_logger(module="services.silence_expiry")

FetchMessagesFn = Callable[[int], Awaitable[tuple[list[dict], dict | None]]]
FormDataFn = Callable[[list[dict]], dict]
NudgeMessageFn = Callable[..., tuple[str, str]]
SendPublicNudgeFn = Callable[..., Awaitable[bool]]
SendSilenceAlertFn = Callable[..., Awaitable[bool]]


@dataclass(frozen=True)
class SilenceExpiredAskResult:
    action: str


async def handle_silence_expired_ask(
    *,
    ticket_id: int,
    state: Any,
    log_path: Path,
    nudge_message_fn: NudgeMessageFn,
    cfg: Any | None = None,
    fetch_customer_messages_fn: FetchMessagesFn | None = None,
    form_data_from_msgs_fn: FormDataFn | None = None,
    send_public_nudge_fn: SendPublicNudgeFn | None = None,
    send_silence_alert_fn: SendSilenceAlertFn | None = None,
    now: datetime | None = None,
) -> SilenceExpiredAskResult:
    """Handle a silence-expired ask event and mark nudge state when sent."""
    cfg = cfg or get_config()
    rec = state.get(ticket_id)
    if rec is None:
        logger.warning("silence_expired_ask_no_state", ticket_id=ticket_id)
        return SilenceExpiredAskResult(action="no_state")

    if rec.nudge_sent_at is not None:
        logger.info(
            "silence_expired_ask_already_sent",
            ticket_id=ticket_id,
            sent_at=rec.nudge_sent_at.isoformat(),
        )
        return SilenceExpiredAskResult(action="already_sent")

    if fetch_customer_messages_fn is None:
        from zd_agent.services.ticket_messages import _fetch_customer_messages

        fetch_customer_messages_fn = _fetch_customer_messages
    if form_data_from_msgs_fn is None:
        from zd_agent.services.ticket_messages import extract_form_data_from_msgs

        form_data_from_msgs_fn = extract_form_data_from_msgs

    customer_msgs, _ticket = await fetch_customer_messages_fn(ticket_id)
    form = form_data_from_msgs_fn(customer_msgs or [])
    customer_name = form.get("name", "")
    lang, body = nudge_message_fn(customer_name=customer_name)
    now = now or datetime.now(timezone.utc)
    verdict = rec.last_verdict or ""

    if cfg.trigger_decider.enable_public_nudge:
        if send_public_nudge_fn is None:
            from zd_agent.services.silence_notifications import (
                send_public_nudge_best_effort,
            )

            send_public_nudge_fn = send_public_nudge_best_effort

        sent = await send_public_nudge_fn(
            ticket_id=ticket_id,
            body=body,
            lang=lang,
        )
        if not sent:
            return SilenceExpiredAskResult(action="public_nudge_failed")

        log_decision(
            log_path=log_path,
            ticket_id=ticket_id,
            verdict=verdict,
            action="public_nudge_sent",
            extra={"lang": lang, "tagged": True},
        )
        action = "public_nudge_sent"
    else:
        if send_silence_alert_fn is None:
            from zd_agent.services.silence_notifications import (
                send_silence_expired_alert_best_effort,
            )

            send_silence_alert_fn = send_silence_expired_alert_best_effort

        await send_silence_alert_fn(ticket_id=ticket_id, verdict=verdict)
        log_decision(
            log_path=log_path,
            ticket_id=ticket_id,
            verdict=verdict,
            action="silence_expired_feishu_alert",
        )
        action = "silence_expired_feishu_alert"

    rec.nudge_sent_at = now
    state.set(ticket_id, rec)
    return SilenceExpiredAskResult(action=action)
