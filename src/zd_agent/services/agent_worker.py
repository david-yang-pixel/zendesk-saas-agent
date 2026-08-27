"""HTTP-free Agent worker process."""

from __future__ import annotations

import asyncio
import signal
from contextlib import suppress
from typing import Any

from aiohttp import web

from zd_agent.config import get_config
from zd_agent.services.agent_lifecycle import (
    AgentLifecycleStore,
    write_worker_heartbeat,
)
from zd_agent.services.app_context import (
    BACKGROUND_TASK_KEYS,
    BACKGROUND_TASK_NAMES,
    SILENCE_TASK_KEY,
)
from zd_agent.services.saas_routes import _outbox_health
from zd_agent.services.app_bootstrap import prepare_saas_app_runtime
from zd_agent.services.app_lifecycle import register_background_lifecycle
from zd_agent.services.saas_background import default_background_runners
from zd_agent.services.trigger_consumer import run_saas_trigger_consumer

WORKER_HEARTBEAT_TASK_KEY = web.AppKey("zd_agent.worker_heartbeat_task", asyncio.Task)
WORKER_MONITOR_TASK_KEY = web.AppKey("zd_agent.worker_monitor_task", asyncio.Task)


async def _monitor_worker_runtime(
    *,
    app: web.Application,
    manager: Any,
    store: AgentLifecycleStore,
    health: dict[str, Any],
) -> None:
    """Apply lifecycle fences and fail the Worker if a critical loop exits."""
    quiesced = False
    intake_quiesced = False
    while True:
        lifecycle = store.read()
        if lifecycle.get("state_error"):
            raise RuntimeError("lifecycle state is unreadable")
        generation = int(lifecycle.get("dispatch_generation") or 0)
        enabled = bool(
            lifecycle.get("desired_state") == "running"
            and lifecycle.get("dispatch_enabled")
        )
        if (
            manager.dispatch_generation != generation
            or manager.accepting_dispatches() != enabled
        ):
            await manager.apply_dispatch_gate(
                enabled=enabled,
                generation=generation,
            )

        tasks = {
            BACKGROUND_TASK_NAMES[key]: app.get(key) for key in BACKGROUND_TASK_KEYS
        }
        if not enabled and not intake_quiesced:
            # Stop every source of new work first, but keep silence_waker alive
            # temporarily because it owns the bounded side-effect reconciler.
            # The dispatch gate already prevents its wakeups from starting a
            # new AgentSession.
            primary_tasks = {
                BACKGROUND_TASK_NAMES[key]: app.get(key)
                for key in BACKGROUND_TASK_KEYS
                if key != SILENCE_TASK_KEY
            }
            for task in primary_tasks.values():
                if task is not None and not task.done():
                    task.cancel()
            for task in primary_tasks.values():
                if task is not None:
                    with suppress(asyncio.CancelledError, Exception):
                        await task
            intake_quiesced = True
        if not enabled and intake_quiesced and not quiesced:
            blockers = {
                key: value
                for key, value in _outbox_health(store.state_dir).items()
                if key != "manual_reconciliation_tickets" and int(value) > 0
            }
            allow_preserved_outbox = bool(
                lifecycle.get("transition_allow_preserved_outbox")
            )
            if int(getattr(manager, "active_count", 0) or 0) == 0 and (
                not blockers or allow_preserved_outbox
            ):
                silence_task = app.get(SILENCE_TASK_KEY)
                if silence_task is not None and not silence_task.done():
                    silence_task.cancel()
                if silence_task is not None:
                    with suppress(asyncio.CancelledError, Exception):
                        await silence_task
                quiesced = True
        elif enabled and quiesced:
            # Background loops are intentionally one-shot lifecycle tasks. A
            # cancelled drain can only resume safely through a fresh process.
            raise RuntimeError("dispatch fence reopened after worker quiesce")

        task_health = {
            name: bool(task is not None and not task.done())
            for name, task in tasks.items()
        }
        health["background_tasks"] = task_health
        health["background_tasks_quiesced"] = quiesced
        if enabled and not all(task_health.values()):
            failed = [name for name, ready in task_health.items() if not ready]
            raise RuntimeError(f"critical background task exited: {','.join(failed)}")
        await asyncio.sleep(0.2)


def create_agent_worker_app(manager: Any) -> web.Application:
    """Build background tasks without binding any HTTP route or port."""
    cfg = get_config()
    app = web.Application()
    runtime = prepare_saas_app_runtime(app=app, cfg=cfg, manager=manager)
    register_background_lifecycle(
        app=app,
        cfg=cfg,
        state_dir=runtime.state_dir,
        state=runtime.state,
        trigger_queue=runtime.trigger_queue,
        manager=manager,
        trigger_consumer_fn=run_saas_trigger_consumer,
        background_runners=default_background_runners(),
    )
    status_store = AgentLifecycleStore(runtime.state_dir)
    health: dict[str, Any] = {
        "background_tasks": {},
        "background_tasks_quiesced": False,
    }

    async def _start_heartbeat(_app: web.Application) -> None:
        _app[WORKER_MONITOR_TASK_KEY] = asyncio.create_task(
            _monitor_worker_runtime(
                app=_app,
                manager=manager,
                store=status_store,
                health=health,
            )
        )
        _app[WORKER_HEARTBEAT_TASK_KEY] = asyncio.create_task(
            write_worker_heartbeat(
                store=status_store,
                manager=manager,
                health=health,
            )
        )

    async def _stop_heartbeat(_app: web.Application) -> None:
        for key in (WORKER_MONITOR_TASK_KEY, WORKER_HEARTBEAT_TASK_KEY):
            task = _app.get(key)
            if task is not None:
                task.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await task

    app.on_startup.append(_start_heartbeat)
    app.on_cleanup.append(_stop_heartbeat)
    return app


async def run_agent_worker(manager: Any) -> None:
    """Run aiohttp lifecycle hooks without creating a listening socket."""
    app = create_agent_worker_app(manager)
    runner = web.AppRunner(app, handle_signals=False)
    await runner.setup()
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGTERM, signal.SIGINT):
        with suppress(NotImplementedError):
            loop.add_signal_handler(signum, stop_event.set)
    try:
        stop_task = asyncio.create_task(stop_event.wait())
        monitor_task = app[WORKER_MONITOR_TASK_KEY]
        done, _pending = await asyncio.wait(
            {stop_task, monitor_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if monitor_task in done:
            await monitor_task
        stop_task.cancel()
        with suppress(asyncio.CancelledError):
            await stop_task
    finally:
        await runner.cleanup()
