"""Lifecycle wiring for the SaaS aiohttp app."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import inspect
from pathlib import Path
from typing import Any, Awaitable, Callable

from aiohttp import web

from zd_agent.log import get_logger
from zd_agent.mention.tracker import MentionTracker
from zd_agent.services.app_context import (
    BACKGROUND_TASK_KEYS,
    MENTION_POLLER_TASK_KEY,
    MENTION_TRACKER_KEY,
    SILENCE_TASK_KEY,
    TRIGGER_TASK_KEY,
    WAITING_POLLER_TASK_KEY,
)

logger = get_logger(module="services.app_lifecycle")

RunnerFn = Callable[..., Awaitable[Any]]


@dataclass(frozen=True)
class BackgroundRunners:
    """Background task runners supplied by the entrypoint."""

    silence_waker: RunnerFn
    waiting_poller: RunnerFn
    mention_poller: RunnerFn


def wire_session_finished_callback(
    *,
    manager: Any,
    state: Any,
    casework_effect_store: Any | None = None,
    trigger_queue: Any | None = None,
) -> None:
    """Wire manager completion callback that updates trigger state."""

    async def _on_session_finished(session: Any) -> None:  # noqa: ANN401
        from zd_agent.services.trigger_state_machine import apply_session_finished

        last_error: Exception | None = None
        for attempt in range(1, 4):
            try:
                apply_session_finished(
                    state,
                    session,
                    casework_effect_store=casework_effect_store,
                )
                return
            except Exception as error:  # noqa: BLE001 - bounded durable retry.
                last_error = error
                logger.warning(
                    "set_last_agent_finish_ts_failed",
                    ticket_id=getattr(session, "ticket_id", None),
                    attempt=attempt,
                    error=str(error),
                )
                if attempt < 3:
                    await asyncio.sleep(0.1 * attempt)
        if trigger_queue is not None:
            record = state.get(getattr(session, "ticket_id", 0))
            if record is not None and record.state == "processing":
                queued = trigger_queue.ensure_processing_recovery(
                    int(session.ticket_id),
                    delivery_attempt_id=(
                        record.delivery_attempt_id
                        or getattr(session, "delivery_attempt_id", "")
                        or getattr(session, "session_id", "")
                    ),
                )
                if inspect.isawaitable(queued):
                    await queued
        raise RuntimeError(
            "finished session state could not be committed durably",
        ) from last_error

    if hasattr(manager, "set_on_finished"):
        manager.set_on_finished(_on_session_finished)


def register_background_lifecycle(
    *,
    app: web.Application,
    cfg: Any,
    state_dir: Path,
    state: Any,
    trigger_queue: Any,
    manager: Any,
    trigger_consumer_fn: Callable[..., Any],
    background_runners: BackgroundRunners,
    create_task_fn: Callable[[Any], Any] = asyncio.create_task,
) -> None:
    """Register startup/shutdown/cleanup hooks for background tasks."""

    async def _on_startup(_app: web.Application) -> None:
        _app[SILENCE_TASK_KEY] = create_task_fn(
            background_runners.silence_waker(
                state=state,
                trigger_queue=trigger_queue,
                scan_interval_seconds=cfg.silence_waker.scan_interval_seconds,
            )
        )
        _app[WAITING_POLLER_TASK_KEY] = create_task_fn(
            background_runners.waiting_poller(
                state=state,
                trigger_queue=trigger_queue,
                scan_interval_seconds=cfg.waiting_poller.scan_interval_seconds,
                customer_wait_timeout_hours=(
                    cfg.waiting_poller.customer_wait_timeout_hours
                ),
            )
        )
        mention_tracker = MentionTracker(state_dir / "processed_mentions.json")
        _app[MENTION_TRACKER_KEY] = mention_tracker
        _app[MENTION_POLLER_TASK_KEY] = create_task_fn(
            background_runners.mention_poller(
                trigger_queue=trigger_queue,
                tracker=mention_tracker,
                poll_interval_seconds=cfg.zendesk.mention_poll_interval_seconds,
            )
        )
        _app[TRIGGER_TASK_KEY] = create_task_fn(
            trigger_consumer_fn(trigger_queue, manager, state),
        )
        logger.info("saas_app_started", port=cfg.webhook.listen_port)

    async def _on_shutdown(_app: web.Application) -> None:
        """Stop new work, then wait briefly for in-flight sessions."""
        for key in BACKGROUND_TASK_KEYS:
            task = _app.get(key)
            if task is not None and not task.done():
                task.cancel()

        grace_s = 30
        if hasattr(manager, "wait_all"):
            in_flight = getattr(manager, "active_count", 0)
            if in_flight > 0:
                logger.info(
                    "shutdown_waiting_in_flight_sessions",
                    in_flight=in_flight,
                    grace_s=grace_s,
                )
                try:
                    await asyncio.wait_for(manager.wait_all(), timeout=grace_s)
                    logger.info("shutdown_in_flight_drained")
                except asyncio.TimeoutError:
                    remaining = getattr(manager, "active_count", 0)
                    logger.warning(
                        "shutdown_grace_expired_forcing_cleanup",
                        still_in_flight=remaining,
                    )

    async def _on_cleanup(_app: web.Application) -> None:
        for key in BACKGROUND_TASK_KEYS:
            task = _app.get(key)
            if task is not None:
                if not task.cancelled() and not task.done():
                    task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass

    app.on_startup.append(_on_startup)
    app.on_shutdown.append(_on_shutdown)
    app.on_cleanup.append(_on_cleanup)
