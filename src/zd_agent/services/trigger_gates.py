"""Entry gates for trigger consumer messages."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from zd_agent.log import get_logger
from zd_agent.services.control_state import is_paused

logger = get_logger(module="services.trigger_gates")

IsPausedFn = Callable[[Any], bool]


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
    if (
        existing_state is not None
        and existing_state.state == "escalated_to_human"
        and source != "mention"
    ):
        logger.info(
            "trigger_skip_escalated",
            ticket_id=ticket_id,
            source=source,
            escalated_reason=existing_state.escalated_reason,
            escalated_at=(
                existing_state.escalated_at.isoformat()
                if existing_state.escalated_at else None
            ),
        )
        decision_log.log_decision(
            log_path=log_path,
            ticket_id=ticket_id,
            verdict="escalated_to_human",
            action="skip",
            extra={
                "source": source,
                "escalated_reason": existing_state.escalated_reason,
            },
        )
        return TriggerGateResult(handled=True, action="escalated_to_human")

    return TriggerGateResult(handled=False)
