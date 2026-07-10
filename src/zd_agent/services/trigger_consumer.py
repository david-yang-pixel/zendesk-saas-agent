"""Background worker for trigger queue messages."""
from __future__ import annotations

import asyncio
import time
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
        await ack(message)
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
    return bool(await retry(message, str(error)))


async def _release_message(queue: Any, message: dict[str, Any]) -> bool:
    release = getattr(queue, "release", None)
    if not callable(release):
        return False
    await release(message)
    return True


async def _defer_message(
    queue: Any,
    message: dict[str, Any],
    error: TriggerDeferredError,
) -> bool:
    defer = getattr(queue, "defer", None)
    if callable(defer):
        await defer(message, error.delay_seconds, error.reason)
        return True
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
