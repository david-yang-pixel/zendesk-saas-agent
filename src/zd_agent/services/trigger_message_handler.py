"""Single-message orchestration for the trigger consumer."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from zd_agent.log import get_logger
from zd_agent.services.customer_activity import handle_customer_activity_gate
from zd_agent.services.direct_dispatch import handle_direct_dispatch_source
from zd_agent.services.human_takeover_dispatch import handle_human_takeover_dispatch
from zd_agent.services.silence_expiry import handle_silence_expired_ask
from zd_agent.services.trigger_gates import handle_trigger_entry_gate
from zd_agent.services.trigger_errors import PublicReplyDeliveryError, TriggerDeferredError
from zd_agent.services.verdict_classification import classify_ticket_messages

logger = get_logger(module="services.trigger_message_handler")

FetchMessagesFn = Callable[[int], Awaitable[tuple[list[dict], dict | None]]]
SilenceExpiredAskFn = Callable[..., Awaitable[Any]]
PostVerdictDispatchFn = Callable[..., Awaitable[Any]]
EscalateFn = Callable[..., Awaitable[Any]]
ClassifyTicketMessagesFn = Callable[..., Awaitable[Any]]
DetectHumanTakeoverFn = Callable[..., Awaitable[tuple[bool, str]]]


@dataclass(frozen=True)
class TriggerMessageResult:
    handled: bool
    action: str
    ticket_id: int
    source: str


@dataclass(frozen=True)
class _VerdictOperationClaim:
    """Short-lived owner fence around classification plus public side effects."""

    previous: Any
    state_revision: int
    operation_id: str


async def handle_trigger_message(
    *,
    msg: dict,
    manager: Any,
    state: Any,
    cfg: Any,
    env: Any,
    log_path: Path,
    silence_expired_ask_fn: SilenceExpiredAskFn | None = None,
    fetch_customer_messages_fn: FetchMessagesFn | None = None,
    post_verdict_dispatch_fn: PostVerdictDispatchFn | None = None,
    escalate_fn: EscalateFn | None = None,
    classify_ticket_messages_fn: ClassifyTicketMessagesFn | None = None,
    detect_human_takeover_fn: DetectHumanTakeoverFn | None = None,
) -> TriggerMessageResult:
    """Handle one trigger queue message and return the terminal action."""
    from zd_agent.services import ticket_messages
    from zd_agent.trigger import decision_log
    from zd_agent.trigger.nudge import nudge_message

    ticket_id = int(msg["ticket_id"])
    source = msg.get("source", "unknown")
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

    if source in {"silence_waker", "silence_expired_ask"}:
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
            raise TriggerDeferredError(
                "active_session_processing",
                delay_seconds=30.0,
            )
        return TriggerMessageResult(True, entry_gate.action, ticket_id, source)

    if source == "mention":
        direct_dispatch = await handle_direct_dispatch_source(
            ticket_id=ticket_id,
            msg=msg,
            manager=manager,
            state=state,
            log_path=log_path,
            cfg=cfg,
            escalate_fn=escalate_fn,
        )
        if direct_dispatch.handled:
            if (
                direct_dispatch.action == "skip_post_agent_cooldown"
                and msg.get("hint")
            ):
                remaining_seconds = max(
                    1.0,
                    direct_dispatch.cooldown_seconds
                    - (direct_dispatch.elapsed_seconds or 0.0),
                )
                raise TriggerDeferredError(
                    "post_agent_cooldown",
                    delay_seconds=remaining_seconds,
                )
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

    if source in {"silence_waker", "silence_expired_ask"} and not (
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

    if escalate_fn is None:
        raise RuntimeError("escalate_fn is required for trigger message handling")
    takeover_dispatch = await handle_human_takeover_dispatch(
        ticket_id=ticket_id,
        ticket_info=ticket_info,
        customer_msgs=customer_msgs,
        state=state,
        log_path=log_path,
        source=source,
        agent_user_id=cfg.zendesk.agent_user_id,
        detect_human_takeover_fn=detect_human_takeover_fn,
        escalate_fn=escalate_fn,
    )
    if takeover_dispatch.handled:
        return TriggerMessageResult(True, "human_takeover", ticket_id, source)

    if source in {"silence_waker", "silence_expired_ask"} and not (
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

    if source == "silence_expired_ask":
        from datetime import datetime, timedelta, timezone

        from zd_agent.services.trigger_routing import latest_customer_message_context

        now = datetime.now(timezone.utc)
        _, latest_cid, _ = latest_customer_message_context(customer_msgs, now=now)
        current = state.get(ticket_id)
        current_last_id = current.last_customer_comment_id if current else None
        if str(latest_cid or "") == str(current_last_id or ""):
            if not _silence_event_is_current(
                state=state,
                ticket_id=ticket_id,
                msg=msg,
            ):
                return TriggerMessageResult(
                    True,
                    "stale_queue_event",
                    ticket_id,
                    source,
                )
            operation_id = (
                "operation:silence_expired_ask:"
                f"{msg.get('_queue_key') or msg.get('delivery_attempt_id') or ticket_id}"
            )
            claim = state.update_if_revision(
                ticket_id,
                expected_revision=msg.get("expected_state_revision"),
                expected_state="in_silence_window",
                expected_active_session_id="",
                mutate=lambda claimed: _claim_silence_ask(
                    claimed,
                    operation_id=operation_id,
                    delivery_attempt_id=str(
                        msg.get("delivery_attempt_id")
                        or msg.get("_queue_key")
                        or f"silence_expired_ask:{ticket_id}"
                    ),
                ),
            )
            if claim is None:
                return TriggerMessageResult(
                    True,
                    "stale_queue_event",
                    ticket_id,
                    source,
                )

            silence_expired_ask_fn = (
                silence_expired_ask_fn or handle_silence_expired_ask
            )
            try:
                ask_result = await silence_expired_ask_fn(
                    ticket_id=ticket_id,
                    state=state,
                    log_path=log_path,
                    nudge_message_fn=nudge_message,
                    delivery_attempt_id=claim.after.delivery_attempt_id,
                    expected_customer_last_id=str(current_last_id or ""),
                    persist_state=False,
                )
            except Exception:
                _release_silence_ask_claim(
                    state=state,
                    ticket_id=ticket_id,
                    claim=claim,
                    operation_id=operation_id,
                    retry_at=now + timedelta(seconds=30),
                )
                raise
            if getattr(ask_result, "action", "") == "new_customer_message":
                _release_silence_ask_claim(
                    state=state,
                    ticket_id=ticket_id,
                    claim=claim,
                    operation_id=operation_id,
                    retry_at=now,
                )
                return TriggerMessageResult(
                    True,
                    "new_customer_message",
                    ticket_id,
                    source,
                )
            finalized = state.update_if_revision(
                ticket_id,
                expected_revision=claim.state_revision,
                expected_state="processing",
                expected_active_session_id=operation_id,
                mutate=lambda _current: _finalize_silence_ask(
                    claim.before,
                    now=now,
                ),
            )
            if finalized is None:
                return TriggerMessageResult(
                    True,
                    "stale_queue_event",
                    ticket_id,
                    source,
                )
            return TriggerMessageResult(
                True,
                "silence_expired_ask",
                ticket_id,
                source,
            )

    if source == "silence_waker":
        direct_dispatch = await handle_direct_dispatch_source(
            ticket_id=ticket_id,
            msg=msg,
            manager=manager,
            state=state,
            log_path=log_path,
            cfg=cfg,
            escalate_fn=escalate_fn,
        )
        if direct_dispatch.handled:
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
        cooldown_seconds=cfg.trigger_decider.post_agent_cooldown_seconds,
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
    verdict_claim = _claim_verdict_operation(
        state=state,
        ticket_id=ticket_id,
        existing=customer_activity.existing,
        delivery_attempt_id=delivery_attempt_id,
    )
    if verdict_claim is None:
        return TriggerMessageResult(True, "stale_queue_event", ticket_id, source)

    classify_ticket_messages_fn = classify_ticket_messages_fn or classify_ticket_messages
    try:
        classification = await classify_ticket_messages_fn(
            ticket_id=ticket_id,
            customer_msgs=customer_msgs,
            model=cfg.trigger_decider.llm_model,
            api_key=env.openai_api_key,
            base_url=cfg.llm.base_url,
            enable_vision_input=cfg.trigger_decider.enable_vision_input,
        )
    except BaseException:
        _release_verdict_operation(
            state=state,
            ticket_id=ticket_id,
            claim=verdict_claim,
        )
        raise

    current_after_classification = state.get(ticket_id)
    if not (
        current_after_classification is not None
        and current_after_classification.state == "processing"
        and current_after_classification.state_revision
        == verdict_claim.state_revision
        and current_after_classification.active_session_id
        == verdict_claim.operation_id
    ):
        return TriggerMessageResult(
            True,
            "stale_queue_event",
            ticket_id,
            source,
        )

    try:
        if post_verdict_dispatch_fn is None:
            raise RuntimeError("post_verdict_dispatch_fn is required")
        conversation_id = str(
            msg.get("conversation_id")
            or ticket_info.get("conversation_id")
            or ""
        )
        messaging_app_id = str(
            msg.get("messaging_app_id")
            or ticket_info.get("messaging_app_id")
            or ""
        )
        if not conversation_id:
            from zd_agent.services.messaging_context import get_messaging_context

            restored_context = get_messaging_context(ticket_id)
            conversation_id = str(restored_context.get("conversation_id") or "")
            messaging_app_id = messaging_app_id or str(
                restored_context.get("app_id") or ""
            )
        await post_verdict_dispatch_fn(
            ticket_id=ticket_id,
            verdict=classification.verdict,
            customer_msgs=customer_msgs,
            ticket_info=ticket_info,
            latest_cid=customer_activity.latest_cid,
            latest_ts=customer_activity.latest_ts,
            existing=customer_activity.existing,
            state=state,
            log_path=log_path,
            source=source,
            now=customer_activity.now,
            cfg=cfg,
            conversation_id=conversation_id or None,
            messaging_app_id=messaging_app_id or None,
            delivery_attempt_id=delivery_attempt_id,
            expected_state_revision=verdict_claim.state_revision,
            expected_active_session_id=verdict_claim.operation_id,
        )
    except BaseException as error:
        if not (
            isinstance(error, PublicReplyDeliveryError) and error.ambiguous
        ):
            _release_verdict_operation(
                state=state,
                ticket_id=ticket_id,
                claim=verdict_claim,
            )
        raise
    # Test or extension dispatchers may not own the final state transition.
    # Never leave their temporary operation claim as a zombie processing state.
    _release_verdict_operation(
        state=state,
        ticket_id=ticket_id,
        claim=verdict_claim,
    )
    return TriggerMessageResult(True, "verdict_dispatched", ticket_id, source)


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
    return bool(
        current is not None
        and current.state == "in_silence_window"
        and isinstance(expected_revision, int)
        and current.state_revision == expected_revision
        and current_window == expected_window
    )


def _claim_verdict_operation(
    *,
    state: Any,
    ticket_id: int,
    existing: Any,
    delivery_attempt_id: str,
) -> _VerdictOperationClaim | None:
    """Claim the exact generation allowed to classify and send a reply."""
    expected_revision = existing.state_revision if existing is not None else None
    expected_state = existing.state if existing is not None else None
    expected_owner = existing.active_session_id if existing is not None else None
    operation_id = f"operation:verdict:{delivery_attempt_id}"

    def _claim(current: Any) -> Any:
        claimed = deepcopy(current) if current is not None else None
        if claimed is None:
            from datetime import datetime, timezone
            from zd_agent.trigger.state_store import TicketState

            claimed = TicketState(
                state="processing",
                first_seen=datetime.now(timezone.utc),
            )
        claimed.state = "processing"
        claimed.active_session_id = operation_id
        claimed.delivery_attempt_id = delivery_attempt_id
        return claimed

    commit = state.update_if_revision(
        ticket_id,
        expected_revision=expected_revision,
        expected_state=expected_state,
        expected_active_session_id=expected_owner,
        mutate=_claim,
    )
    if commit is None:
        return None
    return _VerdictOperationClaim(
        previous=commit.before,
        state_revision=commit.state_revision,
        operation_id=operation_id,
    )


def _release_verdict_operation(
    *,
    state: Any,
    ticket_id: int,
    claim: _VerdictOperationClaim,
) -> bool:
    """Release an unfinished verdict claim only if it still owns the ticket."""
    previous = deepcopy(claim.previous)
    if previous is None:
        return bool(state.delete_if_revision(
            ticket_id,
            expected_revision=claim.state_revision,
            expected_state="processing",
            expected_active_session_id=claim.operation_id,
        ))
    released = state.update_if_revision(
        ticket_id,
        expected_revision=claim.state_revision,
        expected_state="processing",
        expected_active_session_id=claim.operation_id,
        mutate=lambda _current: previous,
    )
    return released is not None


def _claim_silence_ask(
    current: Any,
    *,
    operation_id: str,
    delivery_attempt_id: str,
) -> Any:
    if current is None:
        raise RuntimeError("silence ask claim requires existing ticket state")
    current.state = "processing"
    current.active_session_id = operation_id
    current.delivery_attempt_id = delivery_attempt_id
    return current


def _release_silence_ask_claim(
    *,
    state: Any,
    ticket_id: int,
    claim: Any,
    operation_id: str,
    retry_at: Any,
) -> bool:
    previous = deepcopy(claim.before)
    if previous is None:
        return False
    previous.state = "in_silence_window"
    previous.active_session_id = ""
    previous.silence_window_end = retry_at
    released = state.update_if_revision(
        ticket_id,
        expected_revision=claim.state_revision,
        expected_state="processing",
        expected_active_session_id=operation_id,
        mutate=lambda _current: previous,
    )
    return released is not None


def _finalize_silence_ask(previous: Any, *, now: Any) -> Any:
    if previous is None:
        raise RuntimeError("silence ask finalize requires previous ticket state")
    finalized = deepcopy(previous)
    finalized.state = "waiting_for_issue"
    finalized.active_session_id = ""
    finalized.silence_window_end = None
    finalized.nudge_sent_at = now
    finalized.last_eval_at = now
    return finalized
