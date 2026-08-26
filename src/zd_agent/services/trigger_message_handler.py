"""Single-message orchestration for the trigger consumer."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from zd_agent.casework.domain.enums import ExecutionMode
from zd_agent.casework.policies.rollout import casework_controls_ticket
from zd_agent.integrations.zendesk_mcp import call_zendesk_tool
from zd_agent.log import get_logger
from zd_agent.mention.detector import configured_mention_triggers, is_any_mention
from zd_agent.services.customer_activity import handle_customer_activity_gate
from zd_agent.services.direct_dispatch import handle_direct_dispatch_source
from zd_agent.services.human_takeover_dispatch import handle_human_takeover_dispatch
from zd_agent.services.mention_comments import fetch_recent_ticket_comments
from zd_agent.services.trigger_gates import handle_trigger_entry_gate
from zd_agent.services.trigger_errors import TriggerDeferredError

logger = get_logger(module="services.trigger_message_handler")

_CUSTOMER_CONTEXT_SOURCES = {
    "messaging_webhook",
    "poller_new_msg",
}
_ROLLOUT_DEFER_SECONDS = 60.0

FetchMessagesFn = Callable[[int], Awaitable[tuple[list[dict], dict | None]]]
HumanTakeoverFn = Callable[..., Awaitable[Any]]
DetectHumanTakeoverFn = Callable[..., Awaitable[tuple[bool, str]]]


@dataclass(frozen=True)
class TriggerMessageResult:
    handled: bool
    action: str
    ticket_id: int
    source: str


async def _fetch_mention_candidate_comment(
    ticket_id: int,
    comment_id: str,
) -> dict | None:
    """Read the exact candidate from the bounded newest comment window."""
    comments = await fetch_recent_ticket_comments(
        ticket_id,
        call_fn=call_zendesk_tool,
        target_comment_id=comment_id,
    )
    return next(
        (comment for comment in comments if str(comment.get("id") or "") == comment_id),
        None,
    )


async def handle_trigger_message(
    *,
    msg: dict,
    manager: Any,
    state: Any,
    cfg: Any,
    log_path: Path,
    fetch_customer_messages_fn: FetchMessagesFn | None = None,
    human_takeover_fn: HumanTakeoverFn | None = None,
    detect_human_takeover_fn: DetectHumanTakeoverFn | None = None,
    mention_tracker: Any | None = None,
) -> TriggerMessageResult:
    """Handle one trigger queue message and return the terminal action."""
    from zd_agent.services import ticket_messages
    from zd_agent.trigger import decision_log

    ticket_id = int(msg["ticket_id"])
    source = msg.get("source", "unknown")
    if source == "mention" and mention_tracker is not None:
        raw_comment_id = str(msg.get("comment_id") or "").strip()
        if raw_comment_id and mention_tracker.is_processed(raw_comment_id):
            logger.info(
                "mention_already_processed",
                ticket_id=ticket_id,
                comment_id=raw_comment_id,
            )
            return TriggerMessageResult(
                True,
                "mention_already_processed",
                ticket_id,
                source,
            )
    if source == "mention_candidate":
        if mention_tracker is None:
            raise RuntimeError("mention_tracker is required for mention candidates")
        comment_id = str(
            msg.get("comment_id") or msg.get("latest_comment_id") or ""
        ).strip()
        if not comment_id:
            logger.warning(
                "mention_candidate_missing_comment_id",
                ticket_id=ticket_id,
            )
            return TriggerMessageResult(
                True,
                "mention_candidate_invalid",
                ticket_id,
                source,
            )
        if mention_tracker.is_processed(comment_id):
            logger.info(
                "mention_candidate_already_processed",
                ticket_id=ticket_id,
                comment_id=comment_id,
            )
            return TriggerMessageResult(
                True,
                "mention_already_processed",
                ticket_id,
                source,
            )
        try:
            comment = await _fetch_mention_candidate_comment(ticket_id, comment_id)
        except Exception as error:
            logger.warning(
                "mention_candidate_materialization_failed",
                ticket_id=ticket_id,
                comment_id=comment_id,
                error_type=type(error).__name__,
            )
            raise TriggerDeferredError(
                "ticket_not_materialized",
                delay_seconds=30.0,
            ) from error
        if comment is None:
            raise TriggerDeferredError(
                "ticket_not_materialized",
                delay_seconds=30.0,
            )

        matched_trigger = is_any_mention(
            comment,
            configured_mention_triggers(cfg.zendesk),
        )
        if not matched_trigger:
            mention_tracker.mark_processed(comment_id)
            logger.info(
                "mention_candidate_invalid",
                ticket_id=ticket_id,
                comment_id=comment_id,
            )
            return TriggerMessageResult(
                True,
                "mention_candidate_invalid",
                ticket_id,
                source,
            )

        body = str(comment.get("body") or "")[:500]
        # Keep the durable candidate payload immutable. If shutdown happens
        # after dispatch but before ACK, the released row must replay through
        # the candidate/tracker fence instead of becoming a second raw mention.
        msg = {
            **msg,
            "source": "mention",
            "comment_id": comment_id,
            "hint": f"[来自 @mention 的请求]\n{body}",
            "_queue_key": f"mention:{comment_id}",
        }
        source = "mention"
        logger.info(
            "mention_candidate_validated",
            ticket_id=ticket_id,
            comment_id=comment_id,
            trigger=matched_trigger,
        )
    if source == "silence_expired_ask":
        # Old durable rows are upgraded in place to the v2 wake path.  They
        # must never recreate a legacy generic ask after cutover.
        msg = {**msg, "source": "silence_waker"}
        source = "silence_waker"
        logger.info(
            "legacy_silence_event_upgraded_to_v2",
            ticket_id=ticket_id,
        )
    # A startup-recovery event only owns the ``processing`` generation it saw
    # at entry.  It performs network I/O below, so retain this fence instead
    # of later mutating whichever generation happens to be current.
    startup_recovery = None
    if source == "startup_recovery":
        observed = state.get(ticket_id)
        if observed is not None and observed.state == "processing":
            startup_recovery = (
                observed.state_revision,
                observed.active_session_id,
            )

    if source == "silence_waker":
        if not _silence_event_is_current(state=state, ticket_id=ticket_id, msg=msg):
            current = state.get(ticket_id)
            logger.info(
                "trigger_skip_stale_queue_event",
                ticket_id=ticket_id,
                source=source,
                expected_revision=msg.get("expected_state_revision"),
                current_revision=(current.state_revision if current else None),
                current_state=(current.state if current else None),
            )
            return TriggerMessageResult(
                True,
                "stale_queue_event",
                ticket_id,
                source,
            )

    entry_gate = handle_trigger_entry_gate(
        ticket_id=ticket_id,
        source=source,
        state=state,
        log_path=log_path,
        cfg=cfg,
    )
    if entry_gate.handled:
        if entry_gate.action == "paused":
            raise TriggerDeferredError("paused")
        if entry_gate.action == "active_session_processing":
            if source in _CUSTOMER_CONTEXT_SOURCES:
                current = state.get(ticket_id)
                mark_dirty = getattr(manager, "mark_customer_context_dirty", None)
                if callable(mark_dirty):
                    try:
                        mark_dirty(
                            ticket_id,
                            expected_session_id=(
                                current.active_session_id if current else ""
                            ),
                            source=source,
                            event_id=str(
                                msg.get("_queue_key")
                                or msg.get("delivery_attempt_id")
                                or ""
                            ),
                        )
                    except Exception as error:
                        logger.warning(
                            "active_session_context_signal_failed",
                            ticket_id=ticket_id,
                            source=source,
                            error_type=type(error).__name__,
                        )
            raise TriggerDeferredError(
                "active_session_processing",
                delay_seconds=30.0,
            )
        return TriggerMessageResult(True, entry_gate.action, ticket_id, source)

    if not casework_controls_ticket(
        mode=cfg.casework.mode,
        canary_ticket_ids=cfg.casework.canary_ticket_ids,
        ticket_id=ticket_id,
    ):
        reason = (
            "casework_canary_not_selected"
            if cfg.casework.mode is ExecutionMode.CANARY
            else "casework_mode_inactive"
        )
        logger.info(
            "casework_intake_deferred",
            ticket_id=ticket_id,
            source=source,
            execution_mode=cfg.casework.mode.value,
            reason=reason,
        )
        decision_log.log_decision(
            log_path=log_path,
            ticket_id=ticket_id,
            verdict=reason,
            action="defer",
            extra={"source": source, "execution_mode": cfg.casework.mode.value},
        )
        raise TriggerDeferredError(
            reason,
            delay_seconds=_ROLLOUT_DEFER_SECONDS,
        )

    if source == "mention":
        direct_dispatch = await handle_direct_dispatch_source(
            ticket_id=ticket_id,
            msg=msg,
            manager=manager,
            state=state,
            log_path=log_path,
            cfg=cfg,
        )
        if direct_dispatch.handled:
            if direct_dispatch.action == "skip_post_agent_cooldown" and msg.get("hint"):
                remaining_seconds = max(
                    1.0,
                    direct_dispatch.cooldown_seconds
                    - (direct_dispatch.elapsed_seconds or 0.0),
                )
                raise TriggerDeferredError(
                    "post_agent_cooldown",
                    delay_seconds=remaining_seconds,
                )
            if not direct_dispatch.dispatched:
                # A stale-generation or active-session coalesce did not
                # consume this mention. Keep the durable candidate retryable;
                # marking its tracker here would lose the request forever.
                raise TriggerDeferredError(
                    "mention_dispatch_not_committed",
                    delay_seconds=5.0,
                )
            comment_id = str(msg.get("comment_id") or "").strip()
            if mention_tracker is not None and comment_id:
                mention_tracker.mark_processed(comment_id)
            return TriggerMessageResult(
                True,
                "direct_dispatch",
                ticket_id,
                source,
            )

    fetch_customer_messages_fn = (
        fetch_customer_messages_fn or ticket_messages._fetch_customer_messages
    )
    customer_msgs, ticket_info = await fetch_customer_messages_fn(ticket_id)
    if ticket_info is None:
        raise TriggerDeferredError(
            "ticket_not_materialized",
            delay_seconds=30.0,
        )

    ticket_status = str(ticket_info.get("status") or "").strip().casefold()
    if ticket_status and ticket_status not in {"new", "open"}:
        from zd_agent.services.trigger_state_machine import (
            apply_waiting_poller_out_of_scope,
        )

        current = state.get(ticket_id)
        if current is not None:
            apply_waiting_poller_out_of_scope(
                state,
                ticket_id,
                reason="status_out_of_scope",
                expected_revision=current.state_revision,
                expected_state=current.state,
            )
        logger.info(
            "trigger_status_out_of_scope",
            ticket_id=ticket_id,
            source=source,
            status=ticket_status,
        )
        return TriggerMessageResult(
            True,
            "status_out_of_scope",
            ticket_id,
            source,
        )

    if source == "silence_waker" and not (
        _silence_event_is_current(state=state, ticket_id=ticket_id, msg=msg)
    ):
        return TriggerMessageResult(True, "stale_queue_event", ticket_id, source)

    if not customer_msgs:
        if source == "startup_recovery":
            from datetime import datetime, timedelta, timezone

            if startup_recovery is not None:
                expected_revision, expected_owner = startup_recovery

                # The process-local AgentSession is gone.  Do not replace it
                # with a synthetic owner: release the ticket to a durable,
                # generation-bound silence retry.  The startup event can ACK;
                # the silence waker owns the next materialization attempt.
                def _release_startup_recovery(recovery: Any) -> Any:
                    if recovery is None:
                        raise RuntimeError("startup recovery requires state")
                    recovery.state = "in_silence_window"
                    recovery.active_session_id = ""
                    recovery.last_verdict = "agent_retry"
                    recovery.silence_window_end = datetime.now(
                        timezone.utc,
                    ) + timedelta(seconds=30)
                    return recovery

                state.update_if_revision(
                    ticket_id,
                    expected_revision=expected_revision,
                    expected_state="processing",
                    expected_active_session_id=expected_owner,
                    mutate=_release_startup_recovery,
                )
            return TriggerMessageResult(
                True,
                "startup_recovery_waiting",
                ticket_id,
                source,
            )
        logger.info("trigger_skip_no_customer_msg", ticket_id=ticket_id)
        decision_log.log_decision(
            log_path=log_path,
            ticket_id=ticket_id,
            verdict="no_customer_message",
            action="skip",
        )
        return TriggerMessageResult(True, "no_customer_message", ticket_id, source)

    current_after_fetch = state.get(ticket_id)
    if current_after_fetch is not None and current_after_fetch.state in {
        "owned",
        "externally_owned",
        "escalated_to_human",
    }:
        from zd_agent.services.owned_customer_followup import (
            notify_owned_customer_followup,
        )

        await notify_owned_customer_followup(
            ticket_id=ticket_id,
            customer_msgs=customer_msgs,
            state=state,
            state_dir=Path(state.path).parent,
        )
        return TriggerMessageResult(
            True,
            "owned_customer_followup",
            ticket_id,
            source,
        )

    if human_takeover_fn is None:
        raise RuntimeError("human_takeover_fn is required for trigger message handling")
    takeover_dispatch = await handle_human_takeover_dispatch(
        ticket_id=ticket_id,
        ticket_info=ticket_info,
        customer_msgs=customer_msgs,
        state=state,
        log_path=log_path,
        source=source,
        agent_user_id=cfg.zendesk.agent_user_id,
        detect_human_takeover_fn=detect_human_takeover_fn,
        human_takeover_fn=human_takeover_fn,
    )
    if takeover_dispatch.handled:
        return TriggerMessageResult(True, "human_takeover", ticket_id, source)

    if source == "silence_waker" and not (
        _silence_event_is_current(state=state, ticket_id=ticket_id, msg=msg)
    ):
        return TriggerMessageResult(True, "stale_queue_event", ticket_id, source)

    from zd_agent.services.ticket_message_parsing import (
        conversation_has_pending_form,
    )

    conversation_events = ticket_info.get("_zd_agent_conversation_events") or []
    if conversation_has_pending_form(conversation_events):
        from datetime import datetime, timezone

        from zd_agent.services.trigger_routing import latest_customer_message_context
        from zd_agent.services.trigger_state_machine import apply_waiting_for_form

        now = datetime.now(timezone.utc)
        _, latest_cid, latest_ts = latest_customer_message_context(
            customer_msgs,
            now=now,
        )
        apply_waiting_for_form(
            state,
            ticket_id,
            existing=state.get(ticket_id),
            latest_cid=str(latest_cid or "") or None,
            latest_ts=latest_ts,
            now=now,
            delivery_attempt_id=str(
                msg.get("delivery_attempt_id")
                or msg.get("_queue_key")
                or f"{source}:{ticket_id}:{latest_cid or latest_ts}"
            ),
        )
        logger.info(
            "trigger_waiting_for_answer_bot_form",
            ticket_id=ticket_id,
            source=source,
            last_customer_comment_id=latest_cid,
        )
        return TriggerMessageResult(True, "waiting_for_form", ticket_id, source)

    if source == "silence_waker":
        direct_dispatch = await handle_direct_dispatch_source(
            ticket_id=ticket_id,
            msg=msg,
            manager=manager,
            state=state,
            log_path=log_path,
            cfg=cfg,
        )
        if direct_dispatch.handled:
            if direct_dispatch.action == "agent_offline":
                raise TriggerDeferredError(
                    "agent_draining",
                    delay_seconds=5.0,
                )
            return TriggerMessageResult(
                True,
                "direct_dispatch",
                ticket_id,
                source,
            )

    customer_activity = handle_customer_activity_gate(
        ticket_id=ticket_id,
        customer_msgs=customer_msgs,
        state=state,
        log_path=log_path,
        source=source,
        cooldown_seconds=cfg.intake.post_agent_cooldown_seconds,
    )
    if customer_activity.handled:
        if customer_activity.action == "post_agent_cooldown":
            raise TriggerDeferredError(
                "post_agent_cooldown",
                delay_seconds=customer_activity.defer_seconds,
            )
        return TriggerMessageResult(True, customer_activity.action, ticket_id, source)

    delivery_attempt_id = str(
        msg.get("delivery_attempt_id")
        or msg.get("_queue_key")
        or f"{source}:{ticket_id}:{customer_activity.latest_cid or customer_activity.latest_ts}"
    )
    from zd_agent.services.trigger_state_machine import enter_casework_silence

    silence_minutes = cfg.intake.silence_minutes_default
    transition = enter_casework_silence(
        state,
        ticket_id,
        existing=customer_activity.existing,
        latest_cid=str(customer_activity.latest_cid or "") or None,
        latest_ts=customer_activity.latest_ts,
        now=customer_activity.now,
        intake_reason="casework_ready",
        ask_count_now=(
            customer_activity.existing.ask_count if customer_activity.existing else 0
        ),
        silence_minutes=silence_minutes,
        delivery_attempt_id=delivery_attempt_id,
    )
    decision_log.log_decision(
        log_path=log_path,
        ticket_id=ticket_id,
        verdict="casework_ready",
        action="enter_silence",
        silence_min=silence_minutes,
        extra={"source": source, "execution_mode": cfg.casework.mode.value},
    )
    logger.info(
        "casework_intake_queued",
        ticket_id=ticket_id,
        source=source,
        window_end=(
            transition.silence_window_end.isoformat()
            if transition.silence_window_end is not None
            else ""
        ),
    )
    return TriggerMessageResult(True, "casework_queued", ticket_id, source)


def _silence_event_is_current(*, state: Any, ticket_id: int, msg: dict) -> bool:
    """Check that a delayed silence event still owns its lifecycle generation."""
    current = state.get(ticket_id)
    expected_revision = msg.get("expected_state_revision")
    expected_window = str(msg.get("expected_silence_window_end") or "")
    current_window = (
        current.silence_window_end.isoformat()
        if current is not None and current.silence_window_end is not None
        else ""
    )
    if (
        current is not None
        and current.state == "ownership_pending"
        and current.active_session_id.startswith("dispatch-failure:")
        and current.delivery_attempt_id == str(msg.get("delivery_attempt_id") or "")
    ):
        return True
    return bool(
        current is not None
        and current.state == "in_silence_window"
        and isinstance(expected_revision, int)
        and current.state_revision == expected_revision
        and current_window == expected_window
    )
