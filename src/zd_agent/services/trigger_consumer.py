"""Background worker for trigger queue messages."""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from zd_agent.config import get_config
from zd_agent.log import get_logger
from zd_agent.services.casework_effect_store import casework_effect_reference
from zd_agent.services.cancellation import await_durable_cleanup
from zd_agent.services.trigger_delay import wait_for_trigger_availability
from zd_agent.services.trigger_errors import TriggerDeferredError
from zd_agent.services.trigger_message_handler import handle_trigger_message

logger = get_logger(module="services.trigger_consumer")

HandleMessageFn = Callable[..., Awaitable[Any]]


def _stamp_state_fence(message: dict[str, Any], state: Any) -> None:
    """Freeze the lifecycle generation that this durable event first observed."""
    if message.get("_state_fence_version") == 1:
        return
    try:
        ticket_id = int(message["ticket_id"])
    except (KeyError, TypeError, ValueError):
        return
    current = state.get(ticket_id)
    if "expected_state_revision" not in message:
        message["expected_state_revision"] = (
            current.state_revision if current is not None else None
        )
    expected_revision = message.get("expected_state_revision")
    current_matches = bool(
        current is not None
        and isinstance(expected_revision, int)
        and not isinstance(expected_revision, bool)
        and current.state_revision == expected_revision
    )
    message.setdefault(
        "expected_state",
        current.state if current_matches else "",
    )
    message.setdefault(
        "expected_active_session_id",
        current.active_session_id if current_matches else "",
    )
    message.setdefault(
        "expected_source_event_id",
        (current.last_customer_comment_id or "") if current_matches else "",
    )
    message["_state_fence_version"] = 1


def _dead_letter_owner_token(message: dict[str, Any]) -> str:
    identity = message.get("_queue_key") or message.get("_queue_event_id") or "unknown"
    return f"dead-letter:{identity}"


async def _ack_message(queue: Any, message: dict[str, Any]) -> None:
    ack = getattr(queue, "ack", None)
    if callable(ack):
        acknowledged = await _bounded_durable_call(
            "ack",
            lambda: ack(message),
            message,
        )
        if acknowledged is False:
            logger.warning(
                "trigger_consumer_ack_lost_lease",
                ticket_id=message.get("ticket_id"),
                source=message.get("source"),
            )
        return
    task_done = getattr(queue, "task_done", None)
    if callable(task_done):
        task_done()


async def _retry_message(
    queue: Any,
    message: dict[str, Any],
    error: BaseException,
) -> bool | None:
    retry = getattr(queue, "retry", None)
    if not callable(retry):
        return None
    retried = await _bounded_durable_call(
        "retry",
        lambda: retry(message, str(error)),
        message,
    )
    if retried is None:
        return None
    return bool(retried)


async def _release_message(queue: Any, message: dict[str, Any]) -> bool:
    release = getattr(queue, "release", None)
    if not callable(release):
        return False
    released = await release(message)
    return released is not False


async def _bounded_durable_call(
    operation: str,
    call: Callable[[], Awaitable[Any]],
    message: dict[str, Any],
) -> Any:
    """Retry an idempotent token-fenced queue operation after transient I/O."""
    last_error: Exception | None = None
    for attempt in range(1, 4):
        try:
            return await call()
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 - bounded durable retry.
            last_error = error
            logger.warning(
                "trigger_consumer_durable_operation_retry",
                operation=operation,
                attempt=attempt,
                ticket_id=message.get("ticket_id"),
                error_type=type(error).__name__,
            )
            if attempt < 3:
                await asyncio.sleep(0.1 * attempt)
    raise RuntimeError(
        f"durable queue {operation} failed after retries"
    ) from last_error


async def _release_until_settled(queue: Any, message: dict[str, Any]) -> bool:
    """Never abandon a claimed row merely because its state volume is transient."""
    while True:
        try:
            return await _release_message(queue, message)
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 - retain claim until durable.
            logger.exception(
                "trigger_consumer_release_retry",
                ticket_id=message.get("ticket_id"),
                source=message.get("source"),
                error_type=type(error).__name__,
            )
            await asyncio.sleep(0.2)


async def _durably_release_claim(queue: Any, message: dict[str, Any]) -> bool:
    release_task = asyncio.create_task(_release_until_settled(queue, message))
    try:
        return await asyncio.shield(release_task)
    except asyncio.CancelledError:
        released = await await_durable_cleanup(release_task)
        if released is False:
            logger.info(
                "trigger_consumer_release_already_settled",
                ticket_id=message.get("ticket_id"),
            )
        raise


async def _defer_message(
    queue: Any,
    message: dict[str, Any],
    error: TriggerDeferredError,
) -> bool:
    defer = getattr(queue, "defer", None)
    if callable(defer):
        result = await _bounded_durable_call(
            "defer",
            lambda: defer(message, error.delay_seconds, error.reason),
            message,
        )
        return result is not False
    put = getattr(queue, "put", None)
    if not callable(put):
        return False
    delayed = {
        key: value for key, value in message.items() if not key.startswith("_queue_")
    }
    delayed["available_at"] = time.time() + error.delay_seconds
    await put(delayed)
    return True


async def _own_dead_lettered_trigger(
    *,
    message: dict[str, Any],
    state: Any,
    runtime_failure_ownership_fn: HandleMessageFn | None = None,
) -> bool:
    """Persist typed v2 ownership for one terminal queue failure."""
    if message.get("source") == "mention_candidate":
        # A signed mention candidate is only a discovery hint. If Zendesk never
        # materializes the referenced comment, resolve it without customer or
        # ownership writes; the independent poller remains the fallback.
        logger.warning(
            "mention_candidate_dead_letter_ignored",
            ticket_id=message.get("ticket_id"),
            comment_id=message.get("comment_id"),
        )
        return True
    try:
        ticket_id = int(message["ticket_id"])
    except (KeyError, TypeError, ValueError):
        # The queue row is already a dead letter.  A malformed poison message
        # must remain inspectable without terminating the only consumer task.
        logger.error(
            "trigger_dead_letter_invalid_ticket_id",
            ticket_id=message.get("ticket_id"),
            source=message.get("source"),
        )
        return True
    existing = state.get(ticket_id)
    if existing is not None and existing.state in {
        "processed",
        "escalated_to_human",
        "owned",
        "externally_owned",
        "out_of_scope",
        "abandoned",
    }:
        return True

    owner_token = _dead_letter_owner_token(message)
    resuming_pending = bool(
        existing is not None
        and existing.state == "ownership_pending"
        and existing.active_session_id == owner_token
    )
    fence_present = bool(
        message.get("_state_fence_version") == 1
        and "expected_state_revision" in message
    )
    if not resuming_pending and not fence_present:
        logger.error(
            "trigger_dead_letter_missing_state_fence",
            ticket_id=ticket_id,
            source=message.get("source"),
        )
        return True

    expected_revision = message.get("expected_state_revision")
    expected_state = str(message.get("expected_state") or "")
    expected_owner = str(message.get("expected_active_session_id") or "")
    state_fence_matches = (
        existing is None
        if expected_revision is None
        else bool(
            existing is not None
            and isinstance(expected_revision, int)
            and not isinstance(expected_revision, bool)
            and existing.state_revision == expected_revision
            and (not expected_state or existing.state == expected_state)
            and existing.active_session_id == expected_owner
        )
    )
    if not resuming_pending and not state_fence_matches:
        logger.info(
            "trigger_dead_letter_stale_state_ignored",
            ticket_id=ticket_id,
            source=message.get("source"),
            expected_revision=expected_revision,
            current_revision=(existing.state_revision if existing else None),
            current_state=(existing.state if existing else "deleted"),
        )
        return True

    now = datetime.now(timezone.utc)
    delivery_attempt_id = str(
        message.get("delivery_attempt_id")
        or (existing.delivery_attempt_id if existing else "")
        or message.get("_queue_key")
        or f"trigger-dead-letter:{ticket_id}"
    )
    expected_effect_reference = casework_effect_reference(
        ticket_id,
        f"{delivery_attempt_id}:runtime-failure",
    )
    if resuming_pending:
        claimed = existing
    else:
        from zd_agent.trigger.state_store import TicketState

        def _claim_ownership(current: Any) -> Any:
            claimed_state = current or TicketState(
                state="ownership_pending", first_seen=now
            )
            claimed_state.state = "ownership_pending"
            claimed_state.active_session_id = owner_token
            claimed_state.silence_window_end = None
            claimed_state.delivery_attempt_id = delivery_attempt_id
            claimed_state.ownership_action = "support"
            claimed_state.ownership_reason = "automation_retry_exhausted"
            claimed_state.ownership_effect_reference = expected_effect_reference
            claimed_state.last_verdict = "automation_retry_exhausted_pending"
            return claimed_state

        claim = state.update_if_revision(
            ticket_id,
            expected_revision=expected_revision,
            expected_state=expected_state or None,
            expected_active_session_id=(
                expected_owner if expected_revision is not None else None
            ),
            mutate=_claim_ownership,
        )
        if claim is None:
            logger.info(
                "trigger_dead_letter_claim_lost_generation",
                ticket_id=ticket_id,
                source=message.get("source"),
                expected_revision=expected_revision,
            )
            return True
        claimed = claim.after

    if runtime_failure_ownership_fn is None:
        from zd_agent.services.casework_runtime_failures import (
            persist_runtime_failure_ownership,
        )

        runtime_failure_ownership_fn = persist_runtime_failure_ownership
    attempts = max(3, int(message.get("_queue_attempts") or 3))
    try:
        receipt = await runtime_failure_ownership_fn(
            state_dir=Path(state.path).parent,
            ticket_id=ticket_id,
            delivery_attempt_id=delivery_attempt_id,
            generation=claimed.state_revision,
            source_event_id=claimed.last_customer_comment_id or "",
            snapshot_customer_message_count=None,
            snapshot_customer_last_message_id=(claimed.last_customer_comment_id) or "",
            automation_attempt=attempts,
            failure_type="TriggerQueueExhausted",
        )
    except Exception as error:  # noqa: BLE001 - dead letter remains replayable.
        logger.exception(
            "trigger_dead_letter_ownership_failed",
            ticket_id=ticket_id,
            error_type=type(error).__name__,
        )
        return False
    from zd_agent.casework.domain.enums import OwnershipState

    if receipt.ownership_state is not OwnershipState.CONFIRMED:
        logger.warning(
            "trigger_dead_letter_ownership_pending",
            ticket_id=ticket_id,
            reference=receipt.reference,
        )
        return False

    def _confirm_ownership(current: Any) -> Any:
        if current is None:
            raise RuntimeError("dead-letter ownership claim disappeared")
        current.state = "owned"
        current.active_session_id = ""
        current.silence_window_end = None
        current.delivery_attempt_id = delivery_attempt_id
        current.consecutive_agent_failures = attempts
        current.ownership_action = receipt.ownership_action.value
        current.ownership_reason = receipt.reason_code.value
        current.ownership_effect_reference = receipt.reference
        current.last_verdict = receipt.reason_code.value
        return current

    confirmed = state.update_if_revision(
        ticket_id,
        expected_revision=claimed.state_revision,
        expected_state="ownership_pending",
        expected_active_session_id=owner_token,
        mutate=_confirm_ownership,
    )
    if confirmed is None:
        logger.info(
            "trigger_dead_letter_confirmation_lost_generation",
            ticket_id=ticket_id,
            reference=receipt.reference,
        )
    return True


async def reconcile_dead_lettered_triggers(
    *,
    queue: Any,
    state: Any,
    runtime_failure_ownership_fn: HandleMessageFn | None = None,
    limit: int = 100,
) -> int:
    """Finish the state compensation for dead letters left by a crash.

    Queue failure persistence and typed v2 ownership cannot be one transaction.
    This pass closes that crash gap.  Terminal state and revision checks in
    ``_own_dead_lettered_trigger`` makes repeated startup/periodic passes
    idempotent.
    """
    list_dead_letters = getattr(queue, "list_dead_letters", None)
    if not callable(list_dead_letters):
        return 0
    reconciled = 0
    resolve_dead_letter = getattr(queue, "resolve_dead_letter", None)
    for message in await list_dead_letters(limit=limit):
        try:
            resolved = await _own_dead_lettered_trigger(
                message=message,
                state=state,
                runtime_failure_ownership_fn=runtime_failure_ownership_fn,
            )
        except Exception as error:  # noqa: BLE001 - isolate poison rows.
            logger.exception(
                "trigger_dead_letter_reconciliation_row_failed",
                ticket_id=message.get("ticket_id"),
                source=message.get("source"),
                error_type=type(error).__name__,
            )
            resolved = False
        if resolved and callable(resolve_dead_letter):
            resolved = (await resolve_dead_letter(message)) is not False
        reconciled += int(resolved)
    return reconciled


def default_trigger_log_path(cfg: Any) -> Path:
    return (
        Path(cfg.resources.base_dir)
        / cfg.resources.state_dir
        / "logs"
        / "trigger_log.jsonl"
    )


async def run_trigger_consumer(
    queue: Any,
    manager: Any,
    state: Any,
    *,
    cfg: Any | None = None,
    log_path: Path | None = None,
    handle_message_fn: HandleMessageFn = handle_trigger_message,
    human_takeover_fn: HandleMessageFn | None = None,
    runtime_failure_ownership_fn: HandleMessageFn | None = None,
    mention_tracker: Any | None = None,
) -> None:
    """Consume trigger queue messages and delegate one-message handling."""
    cfg = cfg or get_config()
    log_path = log_path or default_trigger_log_path(cfg)

    while True:
        try:
            msg = await queue.get()
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 - keep the sole consumer alive.
            logger.exception(
                "trigger_consumer_queue_claim_failed",
                error_type=type(error).__name__,
            )
            await asyncio.sleep(0.1)
            continue
        try:
            try:
                if getattr(queue, "schedules_available_at", False):
                    _stamp_state_fence(msg, state)
                if not getattr(queue, "schedules_available_at", False):
                    await wait_for_trigger_availability(msg)
                await handle_message_fn(
                    msg=msg,
                    manager=manager,
                    state=state,
                    cfg=cfg,
                    log_path=log_path,
                    human_takeover_fn=human_takeover_fn,
                    mention_tracker=mention_tracker,
                )
            except TriggerDeferredError as error:
                deferred = await _defer_message(queue, msg, error)
                logger.info(
                    "trigger_consumer_deferred",
                    ticket_id=msg.get("ticket_id"),
                    source=msg.get("source"),
                    reason=error.reason,
                    delay_seconds=error.delay_seconds,
                    deferred=deferred,
                    attempts=msg.get("_queue_attempts"),
                )
                if not deferred:
                    await _own_dead_lettered_trigger(
                        message=msg,
                        state=state,
                        runtime_failure_ownership_fn=runtime_failure_ownership_fn,
                    )
            except Exception as e:
                retried = await _retry_message(queue, msg, e)
                logger.error(
                    "trigger_consumer_error",
                    error=str(e),
                    ticket_id=msg.get("ticket_id"),
                    source=msg.get("source"),
                    retried=retried,
                    attempts=msg.get("_queue_attempts"),
                )
                if retried is False:
                    await _own_dead_lettered_trigger(
                        message=msg,
                        state=state,
                        runtime_failure_ownership_fn=runtime_failure_ownership_fn,
                    )
            else:
                await _ack_message(queue, msg)
        except asyncio.CancelledError:
            release_task = asyncio.create_task(_release_until_settled(queue, msg))
            released = await await_durable_cleanup(release_task)
            logger.info(
                "trigger_consumer_cancelled",
                ticket_id=msg.get("ticket_id"),
                source=msg.get("source"),
                released=released,
                attempts=msg.get("_queue_attempts"),
            )
            raise
        except Exception as error:
            logger.exception(
                "trigger_consumer_durable_disposition_failed",
                ticket_id=msg.get("ticket_id"),
                source=msg.get("source"),
                error_type=type(error).__name__,
            )
            await _durably_release_claim(queue, msg)
            await asyncio.sleep(0.1)


async def run_saas_trigger_consumer(
    queue: Any,
    manager: Any,
    state: Any,
    *,
    cfg: Any | None = None,
    log_path: Path | None = None,
    handle_message_fn: HandleMessageFn = handle_trigger_message,
    mention_tracker: Any | None = None,
) -> None:
    """Run the SaaS trigger consumer with default action adapters."""
    from zd_agent.services.external_ownership import record_human_takeover

    await run_trigger_consumer(
        queue,
        manager,
        state,
        cfg=cfg,
        log_path=log_path,
        handle_message_fn=handle_message_fn,
        human_takeover_fn=record_human_takeover,
        mention_tracker=mention_tracker,
    )
