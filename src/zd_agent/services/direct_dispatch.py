"""Direct dispatch handling for trigger sources that bypass verdict checks."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from zd_agent.log import get_logger
from zd_agent.services.trigger_routing import direct_trigger_cooldown
from zd_agent.trigger.decision_log import log_decision
from zd_agent.trigger.state_store import TicketState

logger = get_logger(module="services.direct_dispatch")

DIRECT_DISPATCH_SOURCES = {"silence_waker", "mention"}
_MAX_DIRECT_DISPATCH_FAILURES = 3


@dataclass(frozen=True)
class DirectDispatchResult:
    handled: bool
    dispatched: bool = False
    action: str = "not_handled"
    cooldown_seconds: int = 0
    elapsed_seconds: float | None = None


class StaleDispatchGeneration(RuntimeError):
    """Raised before task creation when a delayed trigger lost ownership."""


async def handle_direct_dispatch_source(
    *,
    ticket_id: int,
    msg: dict,
    manager: Any,
    state: Any,
    log_path: Path,
    cfg: Any,
    now: datetime | None = None,
    escalate_fn: Any | None = None,
) -> DirectDispatchResult:
    """Handle mention/silence_waker sources that directly dispatch sessions."""
    source = msg.get("source", "unknown")
    if source not in DIRECT_DISPATCH_SOURCES:
        return DirectDispatchResult(handled=False)

    now = now or datetime.now(timezone.utc)
    hint = msg.get("hint") or ""
    # Keep the pre-dispatch generation detached from tracker-owned mutable
    # objects.  The CAS below must compare against the snapshot observed here,
    # not against fields another task may mutate in place while dispatch waits.
    existing_state = deepcopy(state.get(ticket_id))
    if source == "silence_waker" and not _silence_generation_matches(
        existing_state,
        msg,
    ):
        return DirectDispatchResult(handled=True, action="stale_generation")
    cooldown = direct_trigger_cooldown(
        existing=existing_state,
        source=source,
        hint=hint,
        full_cooldown_seconds=cfg.trigger_decider.post_agent_cooldown_seconds,
        now=now,
    )
    if cooldown.should_skip:
        action = "skip"
        result_action = "skip_post_agent_cooldown"
        if source == "silence_waker" and existing_state is not None:
            remaining_seconds = max(
                1.0,
                cooldown.cooldown_seconds - (cooldown.elapsed_seconds or 0.0),
            )
            existing_state.state = "in_silence_window"
            existing_state.silence_window_end = now + timedelta(
                seconds=remaining_seconds,
            )
            state.set(ticket_id, existing_state)
            action = "defer"
            result_action = "defer_post_agent_cooldown"
        logger.info(
            "trigger_skip_post_agent_cooldown_direct",
            ticket_id=ticket_id,
            source=source,
            elapsed_s=round(cooldown.elapsed_seconds or 0, 1),
            cooldown_s=cooldown.cooldown_seconds,
            had_hint=bool(hint),
        )
        log_decision(
            log_path=log_path,
            ticket_id=ticket_id,
            verdict=f"post_agent_cooldown_{source}",
            action=action,
        )
        return DirectDispatchResult(
            handled=True,
            action=result_action,
            cooldown_seconds=cooldown.cooldown_seconds,
            elapsed_seconds=cooldown.elapsed_seconds,
        )

    previous_state = deepcopy(existing_state)
    delivery_attempt_id = str(
        msg.get("delivery_attempt_id")
        or msg.get("_queue_key")
        or f"{source}:{ticket_id}"
    )
    expected_revision = (
        msg.get("expected_state_revision")
        if source == "silence_waker"
        else (existing_state.state_revision if existing_state else None)
    )
    bound_session_id = ""
    bound_revision: int | None = None

    def _bind_session_owner(session: Any) -> None:
        nonlocal bound_revision, bound_session_id
        session_id = str(getattr(session, "session_id", "") or "")
        if not session_id:
            raise RuntimeError("dispatched session is missing session_id")

        def _claim_generation(current: TicketState | None) -> TicketState:
            processing_state = (
                deepcopy(current)
                if current is not None
                else TicketState(state="processing", first_seen=now)
            )
            processing_state.state = "processing"
            processing_state.active_session_id = session_id
            processing_state.delivery_attempt_id = delivery_attempt_id
            return processing_state

        commit = state.update_if_revision(
            ticket_id,
            expected_revision=expected_revision,
            expected_state=(
                "in_silence_window"
                if source == "silence_waker"
                else (existing_state.state if existing_state else None)
            ),
            expected_active_session_id=(
                existing_state.active_session_id if existing_state else None
            ),
            mutate=_claim_generation,
        )
        if commit is None:
            raise StaleDispatchGeneration(
                f"ticket {ticket_id} trigger generation changed before dispatch"
            )
        bound_session_id = session_id
        bound_revision = commit.state_revision

    logger.info(
        "trigger_dispatch_direct",
        ticket_id=ticket_id,
        source=source,
        has_hint=bool(hint),
    )
    try:
        dispatched_session = await manager.dispatch(
            ticket_id,
            dry_run=False,
            trigger_type=f"webhook-{source}",
            hint=hint,
            delivery_attempt_id=delivery_attempt_id,
            on_session_created=_bind_session_owner,
        )
        if not bound_session_id:
            logger.info(
                "trigger_dispatch_coalesced_existing_session",
                ticket_id=ticket_id,
                source=source,
                session_id=str(
                    getattr(dispatched_session, "session_id", "") or ""
                ),
            )
            return DirectDispatchResult(
                handled=True,
                action="coalesced_active_session",
            )
    except StaleDispatchGeneration:
        logger.info(
            "trigger_dispatch_stale_generation",
            ticket_id=ticket_id,
            source=source,
        )
        return DirectDispatchResult(handled=True, action="stale_generation")
    except Exception:
        current = state.get(ticket_id)
        if bound_session_id and not (
            current is not None
            and current.state == "processing"
            and current.active_session_id == bound_session_id
            and current.state_revision == bound_revision
        ):
            logger.info(
                "trigger_dispatch_failure_lost_generation",
                ticket_id=ticket_id,
                source=source,
            )
            raise
        if source == "silence_waker" and previous_state is not None:
            previous_state.consecutive_agent_failures += 1
            previous_state.delivery_attempt_id = delivery_attempt_id
            if (
                previous_state.consecutive_agent_failures
                >= _MAX_DIRECT_DISPATCH_FAILURES
            ):
                try:
                    if escalate_fn is not None:
                        await escalate_fn(
                            ticket_id=ticket_id,
                            reason="agent_dispatch_retry_exhausted",
                            existing=previous_state,
                            state=state,
                            log_path=log_path,
                            source=source,
                            now=now,
                            verdict_str="agent_dispatch_retry_exhausted",
                            latest_cid=previous_state.last_customer_comment_id,
                            latest_ts=previous_state.last_comment_ts,
                            completeness=0,
                            customer_msgs=[],
                            extra_log={
                                "dispatch_failures": (
                                    previous_state.consecutive_agent_failures
                                ),
                            },
                        )
                    else:
                        from zd_agent.services.trigger_state_machine import (
                            apply_escalated_to_human,
                        )

                        apply_escalated_to_human(
                            state,
                            ticket_id,
                            existing=previous_state,
                            latest_cid=previous_state.last_customer_comment_id,
                            latest_ts=previous_state.last_comment_ts,
                            now=now,
                            verdict_str="agent_dispatch_retry_exhausted",
                            reason="agent_dispatch_retry_exhausted",
                        )
                except Exception as escalation_error:  # noqa: BLE001
                    logger.exception(
                        "trigger_dispatch_exhaustion_escalation_failed",
                        ticket_id=ticket_id,
                        error=str(escalation_error)[:200],
                    )
                    from zd_agent.services.trigger_state_machine import (
                        apply_escalated_to_human,
                    )

                    apply_escalated_to_human(
                        state,
                        ticket_id,
                        existing=previous_state,
                        latest_cid=previous_state.last_customer_comment_id,
                        latest_ts=previous_state.last_comment_ts,
                        now=now,
                        verdict_str="agent_dispatch_retry_exhausted",
                        reason="agent_dispatch_retry_exhausted",
                    )
                logger.error(
                    "trigger_dispatch_retry_exhausted",
                    ticket_id=ticket_id,
                    source=source,
                    failures=previous_state.consecutive_agent_failures,
                )
                return DirectDispatchResult(
                    handled=True,
                    action="escalated_dispatch_retry_exhausted",
                )

            previous_state.state = "in_silence_window"
            previous_state.last_verdict = "agent_retry"
            backoff_seconds = min(
                300,
                30 * (2 ** (previous_state.consecutive_agent_failures - 1)),
            )
            previous_state.silence_window_end = now + timedelta(
                seconds=backoff_seconds,
            )
            state.set(ticket_id, previous_state)
        elif previous_state is None:
            state.delete(ticket_id)
        else:
            state.set(ticket_id, previous_state)
        logger.exception(
            "trigger_dispatch_direct_failed_state_restored",
            ticket_id=ticket_id,
            source=source,
            restored_state=(previous_state.state if previous_state else None),
        )
        raise
    return DirectDispatchResult(handled=True, dispatched=True, action="dispatch")


def _silence_generation_matches(current: TicketState | None, msg: dict) -> bool:
    if current is None or current.state != "in_silence_window":
        return False
    expected_revision = msg.get("expected_state_revision")
    expected_window = str(msg.get("expected_silence_window_end") or "")
    current_window = (
        current.silence_window_end.isoformat()
        if current.silence_window_end is not None
        else ""
    )
    return bool(
        isinstance(expected_revision, int)
        and current.state_revision == expected_revision
        and current_window == expected_window
    )
