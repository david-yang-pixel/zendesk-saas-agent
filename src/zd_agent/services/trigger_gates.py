"""Entry gates for trigger consumer messages."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from zd_agent.log import get_logger
from zd_agent.services.control_state import is_paused

logger = get_logger(module="services.trigger_gates")

IsPausedFn = Callable[[Any], bool]

_CUSTOMER_ACTIVITY_SOURCES = {
    "zendesk_webhook",
    "messaging_webhook",
    "poller_new_msg",
    "intake_backfill",
}
_CUSTOMER_WAIT_TIMEOUT_VERDICTS = {
    "customer_response_timeout",
    "form_submission_timeout",
}


@dataclass(frozen=True)
class TriggerGateResult:
    handled: bool
    action: str = "continue"


def handle_trigger_entry_gate(
    *,
    ticket_id: int,
    source: str,
    state: Any,
    log_path: Path,
    cfg: Any,
    is_paused_fn: IsPausedFn | None = None,
) -> TriggerGateResult:
    """Apply global skip gates before source-specific trigger handling."""
    from zd_agent.trigger import decision_log

    min_ticket_id = cfg.zendesk.min_ticket_id
    if min_ticket_id and ticket_id < min_ticket_id:
        logger.info(
            "trigger_skip_below_min_ticket_id",
            ticket_id=ticket_id,
            source=source,
            min_ticket_id=min_ticket_id,
        )
        decision_log.log_decision(
            log_path=log_path,
            ticket_id=ticket_id,
            verdict="below_min_ticket_id",
            action="skip",
            extra={"source": source, "min_ticket_id": min_ticket_id},
        )
        return TriggerGateResult(handled=True, action="below_min_ticket_id")

    is_paused_fn = is_paused_fn or is_paused
    if is_paused_fn(cfg):
        logger.info(
            "trigger_skip_paused",
            ticket_id=ticket_id,
            source=source,
        )
        decision_log.log_decision(
            log_path=log_path,
            ticket_id=ticket_id,
            verdict="paused",
            action="skip",
            extra={"source": source},
        )
        return TriggerGateResult(handled=True, action="paused")

    existing_state = state.get(ticket_id)
    if existing_state is not None and existing_state.state == "manual_reconciliation":
        logger.error(
            "trigger_skip_manual_reconciliation",
            ticket_id=ticket_id,
            source=source,
            delivery_attempt_id=existing_state.delivery_attempt_id,
        )
        decision_log.log_decision(
            log_path=log_path,
            ticket_id=ticket_id,
            verdict="manual_reconciliation",
            action="skip",
            extra={"source": source},
        )
        return TriggerGateResult(
            handled=True,
            action="manual_reconciliation_required",
        )
    if (
        existing_state is not None
        and existing_state.active_session_id
        and (
            (existing_state.state == "processing" and source != "startup_recovery")
            or (
                existing_state.state == "ownership_pending"
                and source != "silence_waker"
            )
        )
    ):
        logger.info(
            "trigger_coalesced_into_active_session",
            ticket_id=ticket_id,
            source=source,
            active_session_id=existing_state.active_session_id,
        )
        decision_log.log_decision(
            log_path=log_path,
            ticket_id=ticket_id,
            verdict="active_session_processing",
            action="coalesce",
            extra={
                "source": source,
                "active_session_id": existing_state.active_session_id,
            },
        )
        return TriggerGateResult(
            handled=True,
            action="active_session_processing",
        )

    timed_out_wait_may_have_new_customer_activity = bool(
        existing_state is not None
        and existing_state.state == "out_of_scope"
        and existing_state.last_verdict in _CUSTOMER_WAIT_TIMEOUT_VERDICTS
        and source in _CUSTOMER_ACTIVITY_SOURCES
    )
    owned_ticket_may_have_new_customer_activity = bool(
        existing_state is not None
        and existing_state.state
        in {"escalated_to_human", "owned", "externally_owned"}
        and source in _CUSTOMER_ACTIVITY_SOURCES
    )
    if (
        existing_state is not None
        and existing_state.state
        in {
            "escalated_to_human",
            "owned",
            "externally_owned",
            "out_of_scope",
        }
        and source != "mention"
        and not timed_out_wait_may_have_new_customer_activity
        and not owned_ticket_may_have_new_customer_activity
    ):
        logger.info(
            "trigger_skip_owned",
            ticket_id=ticket_id,
            source=source,
            escalated_reason=existing_state.escalated_reason,
            escalated_at=(
                existing_state.escalated_at.isoformat()
                if existing_state.escalated_at
                else None
            ),
        )
        decision_log.log_decision(
            log_path=log_path,
            ticket_id=ticket_id,
            verdict=existing_state.state,
            action="skip",
            extra={
                "source": source,
                "escalated_reason": existing_state.escalated_reason,
            },
        )
        return TriggerGateResult(handled=True, action=existing_state.state)

    return TriggerGateResult(handled=False)
