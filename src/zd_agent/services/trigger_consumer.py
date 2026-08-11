"""Background worker for trigger queue messages."""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from zd_agent.config import EnvSettings, get_config
from zd_agent.log import get_logger
from zd_agent.services.trigger_delay import wait_for_trigger_availability
from zd_agent.services.trigger_errors import TriggerDeferredError
from zd_agent.services.trigger_message_handler import handle_trigger_message

logger = get_logger(module="services.trigger_consumer")

HandleMessageFn = Callable[..., Awaitable[Any]]


async def _ack_message(queue: Any, message: dict[str, Any]) -> None:
    ack = getattr(queue, "ack", None)
    if callable(ack):
        acknowledged = await ack(message)
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
    retried = await retry(message, str(error))
    if retried is None:
        return None
    return bool(retried)


async def _release_message(queue: Any, message: dict[str, Any]) -> bool:
    release = getattr(queue, "release", None)
    if not callable(release):
        return False
    released = await release(message)
    return released is not False


async def _defer_message(
    queue: Any,
    message: dict[str, Any],
    error: TriggerDeferredError,
) -> bool:
    defer = getattr(queue, "defer", None)
    if callable(defer):
        result = await defer(message, error.delay_seconds, error.reason)
        return result is not False
    put = getattr(queue, "put", None)
    if not callable(put):
        return False
    delayed = {
        key: value
        for key, value in message.items()
        if not key.startswith("_queue_")
    }
    delayed["available_at"] = time.time() + error.delay_seconds
    await put(delayed)
    return True


async def _escalate_dead_lettered_trigger(
    *,
    message: dict[str, Any],
    state: Any,
    log_path: Path,
    escalate_fn: HandleMessageFn | None,
    runtime_failure_ownership_fn: HandleMessageFn | None = None,
) -> bool:
    """Persist typed v2 ownership for one terminal queue failure."""
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
        "abandoned",
    }:
        return True

    expected_revision = message.get("expected_state_revision")
    if (
        existing is not None
        and isinstance(expected_revision, int)
        and existing.state_revision != expected_revision
    ):
        logger.info(
            "trigger_dead_letter_stale_state_ignored",
            ticket_id=ticket_id,
            source=message.get("source"),
            expected_revision=expected_revision,
            current_revision=existing.state_revision,
            current_state=existing.state,
        )
        return True

    now = datetime.now(timezone.utc)
    del escalate_fn
    if runtime_failure_ownership_fn is None:
        from zd_agent.services.casework_runtime_failures import (
            persist_runtime_failure_ownership,
        )

        runtime_failure_ownership_fn = persist_runtime_failure_ownership
    attempts = max(3, int(message.get("_queue_attempts") or 3))
    delivery_attempt_id = str(
        message.get("delivery_attempt_id")
        or (existing.delivery_attempt_id if existing else "")
        or message.get("_queue_key")
        or f"trigger-dead-letter:{ticket_id}"
    )
    try:
        receipt = await runtime_failure_ownership_fn(
            state_dir=Path(state.path).parent,
            ticket_id=ticket_id,
            delivery_attempt_id=delivery_attempt_id,
            generation=(existing.state_revision if existing else 0),
            source_event_id=(
                existing.last_customer_comment_id if existing else ""
            ) or "",
            snapshot_customer_message_count=None,
            snapshot_customer_last_message_id=(
                existing.last_customer_comment_id if existing else ""
            ) or "",
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
    from zd_agent.trigger.state_store import TicketState

    owned = existing or TicketState(state="owned", first_seen=now)
    owned.state = "owned"
    owned.active_session_id = ""
    owned.silence_window_end = None
    owned.delivery_attempt_id = delivery_attempt_id
    owned.consecutive_agent_failures = attempts
    owned.ownership_action = receipt.ownership_action.value
    owned.ownership_reason = receipt.reason_code.value
    owned.ownership_effect_reference = receipt.reference
    owned.last_verdict = receipt.reason_code.value
    state.set(ticket_id, owned)
    return True


async def reconcile_dead_lettered_triggers(
    *,
    queue: Any,
    state: Any,
    log_path: Path,
    escalate_fn: HandleMessageFn | None,
    runtime_failure_ownership_fn: HandleMessageFn | None = None,
    limit: int = 100,
) -> int:
    """Finish the state compensation for dead letters left by a crash.

    Queue failure persistence and human escalation cannot be one transaction.
    This pass closes that crash gap.  Terminal state and revision checks in
    ``_escalate_dead_lettered_trigger`` make repeated startup/periodic passes
    idempotent.
    """
    list_dead_letters = getattr(queue, "list_dead_letters", None)
    if not callable(list_dead_letters):
        return 0
    reconciled = 0
    for message in await list_dead_letters(limit=limit):
        resolved = await _escalate_dead_lettered_trigger(
            message=message,
            state=state,
            log_path=log_path,
            escalate_fn=escalate_fn,
            runtime_failure_ownership_fn=runtime_failure_ownership_fn,
        )
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
    env: Any | None = None,
    log_path: Path | None = None,
    handle_message_fn: HandleMessageFn = handle_trigger_message,
    silence_expired_ask_fn: HandleMessageFn | None = None,
    post_verdict_dispatch_fn: HandleMessageFn | None = None,
    escalate_fn: HandleMessageFn | None = None,
    runtime_failure_ownership_fn: HandleMessageFn | None = None,
) -> None:
    """Consume trigger queue messages and delegate one-message handling."""
    cfg = cfg or get_config()
    env = env or EnvSettings()
    log_path = log_path or default_trigger_log_path(cfg)

    while True:
        msg = await queue.get()
        try:
            if not getattr(queue, "schedules_available_at", False):
                await wait_for_trigger_availability(msg)
            await handle_message_fn(
                msg=msg,
                manager=manager,
                state=state,
                cfg=cfg,
                env=env,
                log_path=log_path,
                silence_expired_ask_fn=silence_expired_ask_fn,
                post_verdict_dispatch_fn=post_verdict_dispatch_fn,
                escalate_fn=escalate_fn,
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
                await _escalate_dead_lettered_trigger(
                    message=msg,
                    state=state,
                    log_path=log_path,
                    escalate_fn=escalate_fn,
                    runtime_failure_ownership_fn=runtime_failure_ownership_fn,
                )
        except asyncio.CancelledError:
            released = await asyncio.shield(_release_message(queue, msg))
            logger.info(
                "trigger_consumer_cancelled",
                ticket_id=msg.get("ticket_id"),
                source=msg.get("source"),
                released=released,
                attempts=msg.get("_queue_attempts"),
            )
            raise
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
                await _escalate_dead_lettered_trigger(
                    message=msg,
                    state=state,
                    log_path=log_path,
                    escalate_fn=escalate_fn,
                    runtime_failure_ownership_fn=runtime_failure_ownership_fn,
                )
        else:
            await _ack_message(queue, msg)


async def run_saas_trigger_consumer(
    queue: Any,
    manager: Any,
    state: Any,
    *,
    cfg: Any | None = None,
    env: Any | None = None,
    log_path: Path | None = None,
    handle_message_fn: HandleMessageFn = handle_trigger_message,
) -> None:
    """Run the SaaS trigger consumer with default action adapters."""
    from zd_agent.services.trigger_actions import (
        handle_silence_expired_ask_action,
        post_verdict_dispatch_action,
        set_escalated_to_human_action,
    )

    await run_trigger_consumer(
        queue,
        manager,
        state,
        cfg=cfg,
        env=env,
        log_path=log_path,
        handle_message_fn=handle_message_fn,
        silence_expired_ask_fn=handle_silence_expired_ask_action,
        post_verdict_dispatch_fn=post_verdict_dispatch_action,
        escalate_fn=set_escalated_to_human_action,
    )
