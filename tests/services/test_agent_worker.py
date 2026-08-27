from __future__ import annotations

import asyncio
from contextlib import suppress

import pytest
from aiohttp import web

from zd_agent.services import agent_worker
from zd_agent.services.agent_lifecycle import AgentLifecycleStore
from zd_agent.services.agent_worker import _monitor_worker_runtime
from zd_agent.services.app_context import BACKGROUND_TASK_KEYS


class _Manager:
    def __init__(self, *, enabled: bool = True, generation: int = 0) -> None:
        self.enabled = enabled
        self.dispatch_generation = generation

    def accepting_dispatches(self) -> bool:
        return self.enabled

    async def apply_dispatch_gate(self, *, enabled: bool, generation: int) -> None:
        self.enabled = enabled
        self.dispatch_generation = generation


async def _forever() -> None:
    await asyncio.Event().wait()


@pytest.mark.asyncio
async def test_worker_acknowledges_fence_only_after_background_tasks_stop(
    tmp_path,
) -> None:
    store = AgentLifecycleStore(tmp_path / ".state")
    store.write(
        {
            "desired_state": "running",
            "dispatch_enabled": False,
            "dispatch_generation": 3,
            "generation": 1,
            "accept_after": 0,
        }
    )
    app = web.Application()
    tasks = []
    for key in BACKGROUND_TASK_KEYS:
        task = asyncio.create_task(_forever())
        app[key] = task
        tasks.append(task)
    manager = _Manager()
    health: dict = {}
    monitor = asyncio.create_task(
        _monitor_worker_runtime(
            app=app,
            manager=manager,
            store=store,
            health=health,
        )
    )

    for _ in range(20):
        if health.get("background_tasks_quiesced"):
            break
        await asyncio.sleep(0.01)

    assert manager.enabled is False
    assert manager.dispatch_generation == 3
    assert health["background_tasks_quiesced"] is True
    assert all(task.done() for task in tasks)
    monitor.cancel()
    with suppress(asyncio.CancelledError):
        await monitor


@pytest.mark.asyncio
async def test_dependency_stop_quiesces_with_preserved_outbox(
    monkeypatch,
    tmp_path,
) -> None:
    store = AgentLifecycleStore(tmp_path / ".state")
    store.write(
        {
            "desired_state": "running",
            "dispatch_enabled": False,
            "dispatch_generation": 4,
            "generation": 1,
            "accept_after": 0,
            "transition_action": "stop",
            "transition_allow_preserved_outbox": True,
        }
    )
    monkeypatch.setattr(
        agent_worker,
        "_outbox_health",
        lambda _state_dir: {"public_reply_ambiguous": 2},
    )
    app = web.Application()
    tasks = []
    for key in BACKGROUND_TASK_KEYS:
        task = asyncio.create_task(_forever())
        app[key] = task
        tasks.append(task)
    health: dict = {}
    monitor = asyncio.create_task(
        _monitor_worker_runtime(
            app=app,
            manager=_Manager(),
            store=store,
            health=health,
        )
    )

    for _ in range(20):
        if health.get("background_tasks_quiesced"):
            break
        await asyncio.sleep(0.01)

    assert health["background_tasks_quiesced"] is True
    assert all(task.done() for task in tasks)
    monitor.cancel()
    with suppress(asyncio.CancelledError):
        await monitor


@pytest.mark.asyncio
async def test_worker_fails_when_a_critical_background_task_exits(tmp_path) -> None:
    store = AgentLifecycleStore(tmp_path / ".state")
    store.write(
        {
            "desired_state": "running",
            "dispatch_enabled": True,
            "dispatch_generation": 0,
            "generation": 1,
            "accept_after": 0,
        }
    )
    app = web.Application()
    tasks = []
    for index, key in enumerate(BACKGROUND_TASK_KEYS):
        task = asyncio.create_task(asyncio.sleep(0) if index == 0 else _forever())
        app[key] = task
        tasks.append(task)
    await asyncio.sleep(0)

    with pytest.raises(RuntimeError, match="critical background task exited"):
        await _monitor_worker_runtime(
            app=app,
            manager=_Manager(),
            store=store,
            health={},
        )

    for task in tasks:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


@pytest.mark.asyncio
async def test_worker_exits_when_initialized_lifecycle_file_disappears(
    tmp_path,
) -> None:
    store = AgentLifecycleStore(tmp_path / ".state")
    store.write(
        {
            "desired_state": "running",
            "dispatch_enabled": True,
            "dispatch_generation": 0,
            "generation": 1,
            "accept_after": 0,
        }
    )
    store.path.unlink()
    app = web.Application()
    tasks = []
    for key in BACKGROUND_TASK_KEYS:
        task = asyncio.create_task(_forever())
        app[key] = task
        tasks.append(task)

    with pytest.raises(RuntimeError, match="lifecycle state is unreadable"):
        await _monitor_worker_runtime(
            app=app,
            manager=_Manager(),
            store=store,
            health={},
        )

    for task in tasks:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
