"""Tests for SaaS app lifecycle wiring."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import web

from zd_agent.config import AppConfig
from zd_agent.services.app_context import (
    BACKGROUND_TASK_KEYS,
    MENTION_POLLER_TASK_KEY,
    MENTION_TRACKER_KEY,
    SILENCE_TASK_KEY,
    TRIGGER_TASK_KEY,
    WAITING_POLLER_TASK_KEY,
)
from zd_agent.services.app_lifecycle import (
    BackgroundRunners,
    register_background_lifecycle,
    wire_session_finished_callback,
)
from zd_agent.services.durable_trigger_queue import DurableTriggerQueue
from zd_agent.session.agent_session import AgentSession, SessionStatus
from zd_agent.trigger.state_store import TicketState, TicketStateTracker


class FakeTask:
    def __init__(self) -> None:
        self.cancelled_flag = False

    def done(self) -> bool:
        return False

    def cancel(self) -> None:
        self.cancelled_flag = True

    def cancelled(self) -> bool:
        return self.cancelled_flag

    def __await__(self):
        async def _noop():
            return None

        return _noop().__await__()


def _cfg() -> AppConfig:
    cfg = AppConfig()
    cfg.silence_waker.scan_interval_seconds = 11
    cfg.waiting_poller.scan_interval_seconds = 22
    cfg.waiting_poller.customer_wait_timeout_hours = 33
    cfg.zendesk.mention_poll_interval_seconds = 55
    return cfg


@pytest.mark.asyncio
async def test_wire_session_finished_callback_marks_reply_session_processed(
    tmp_path,
) -> None:
    session = AgentSession(ticket_id=2808, trigger_type="webhook-mention")
    state = TicketStateTracker(tmp_path / "tickets.json")
    state.set(
        "2808",
        TicketState(
            state="processing",
            first_seen=datetime.now(timezone.utc),
            active_session_id=session.session_id,
        ),
    )
    manager = MagicMock()

    wire_session_finished_callback(manager=manager, state=state)

    callback = manager.set_on_finished.call_args.args[0]
    session.status = SessionStatus.COMPLETED
    session.reply_sent = True
    await callback(session)

    assert state.get("2808").state == "processed"
    assert state.get("2808").last_agent_finish_ts is not None


@pytest.mark.asyncio
async def test_finished_callback_retries_a_transient_state_commit_failure(
    tmp_path,
    monkeypatch,
) -> None:
    session = AgentSession(ticket_id=2809, trigger_type="webhook")
    state = TicketStateTracker(tmp_path / "tickets.json")
    state.set(
        2809,
        TicketState(
            state="processing",
            first_seen=datetime.now(timezone.utc),
            active_session_id=session.session_id,
        ),
    )
    original_save = state._save
    attempts = 0

    def flaky_save(serialized) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("temporary state write failure")
        original_save(serialized)

    monkeypatch.setattr(state, "_save", flaky_save)
    manager = MagicMock()
    wire_session_finished_callback(manager=manager, state=state)
    callback = manager.set_on_finished.call_args.args[0]
    session.status = SessionStatus.COMPLETED
    session.reply_sent = True

    await callback(session)

    record = state.get(2809)
    assert record is not None
    assert record.state == "processed"
    assert record.active_session_id == ""
    assert attempts == 2


@pytest.mark.asyncio
async def test_finished_callback_queues_new_generation_after_persistent_failure(
    tmp_path,
    monkeypatch,
) -> None:
    queue = DurableTriggerQueue(
        tmp_path / "trigger_queue.sqlite3",
        max_attempts=1,
        retry_base_seconds=0,
    )
    assert queue.ensure_processing_recovery(
        2810,
        delivery_attempt_id="old-delivery",
    )
    old = await queue.get()
    assert await queue.retry(old, "old recovery exhausted") is False
    dead_letter = (await queue.list_dead_letters())[0]
    assert await queue.resolve_dead_letter(dead_letter) is True

    session = AgentSession(ticket_id=2810, trigger_type="webhook")
    session.delivery_attempt_id = "new-delivery"
    session.status = SessionStatus.COMPLETED
    session.reply_sent = True
    state = TicketStateTracker(tmp_path / "tickets.json")
    state.set(
        2810,
        TicketState(
            state="processing",
            first_seen=datetime.now(timezone.utc),
            active_session_id=session.session_id,
            delivery_attempt_id=session.delivery_attempt_id,
        ),
    )

    def fail_save(_serialized) -> None:
        raise OSError("state volume unavailable")

    monkeypatch.setattr(state, "_save", fail_save)
    manager = MagicMock()
    wire_session_finished_callback(
        manager=manager,
        state=state,
        trigger_queue=queue,
    )
    callback = manager.set_on_finished.call_args.args[0]

    with pytest.raises(RuntimeError, match="could not be committed"):
        await callback(session)

    recovery = await queue.get()
    assert recovery["delivery_attempt_id"] == "new-delivery"
    assert recovery["_queue_key"] != old["_queue_key"]
    await queue.ack(recovery)


@pytest.mark.asyncio
async def test_startup_creates_background_tasks(tmp_path) -> None:
    app = web.Application()
    trigger_queue = MagicMock()
    state = TicketStateTracker(tmp_path / "tickets.json")
    manager = MagicMock()
    created: list[object] = []
    calls: list[tuple[str, dict]] = []

    async def fake_silence_waker(**kwargs):
        calls.append(("silence", kwargs))

    async def fake_waiting_poller(**kwargs):
        calls.append(("waiting", kwargs))

    async def fake_mention_poller(**kwargs):
        calls.append(("mention", kwargs))

    def fake_create_task(coro):
        coro.close()
        task = FakeTask()
        created.append(task)
        return task

    register_background_lifecycle(
        app=app,
        cfg=_cfg(),
        state_dir=tmp_path,
        state=state,
        trigger_queue=trigger_queue,
        manager=manager,
        trigger_consumer_fn=AsyncMock(),
        background_runners=BackgroundRunners(
            silence_waker=fake_silence_waker,
            waiting_poller=fake_waiting_poller,
            mention_poller=fake_mention_poller,
        ),
        create_task_fn=fake_create_task,
    )

    await app.on_startup[-1](app)

    assert len(created) == 4
    assert app[SILENCE_TASK_KEY] is created[0]
    assert app[WAITING_POLLER_TASK_KEY] is created[1]
    assert app[MENTION_POLLER_TASK_KEY] is created[2]
    assert app[TRIGGER_TASK_KEY] is created[3]
    assert MENTION_TRACKER_KEY in app
    assert calls == []


@pytest.mark.asyncio
async def test_startup_wires_background_runner_arguments(tmp_path) -> None:
    app = web.Application()
    trigger_queue = MagicMock()
    state = TicketStateTracker(tmp_path / "tickets.json")
    manager = MagicMock()
    created_coros: list[object] = []

    async def fake_silence_waker(**kwargs):
        return kwargs

    async def fake_waiting_poller(**kwargs):
        return kwargs

    async def fake_mention_poller(**kwargs):
        return kwargs

    async def fake_trigger_consumer(*args):
        return args

    def fake_create_task(coro):
        created_coros.append(coro)
        task = FakeTask()
        return task

    register_background_lifecycle(
        app=app,
        cfg=_cfg(),
        state_dir=tmp_path,
        state=state,
        trigger_queue=trigger_queue,
        manager=manager,
        trigger_consumer_fn=fake_trigger_consumer,
        background_runners=BackgroundRunners(
            silence_waker=fake_silence_waker,
            waiting_poller=fake_waiting_poller,
            mention_poller=fake_mention_poller,
        ),
        create_task_fn=fake_create_task,
    )

    await app.on_startup[-1](app)

    silence_kwargs = await created_coros[0]
    waiting_kwargs = await created_coros[1]
    mention_kwargs = await created_coros[2]
    trigger_args = await created_coros[3]

    assert silence_kwargs == {
        "state": state,
        "trigger_queue": trigger_queue,
        "scan_interval_seconds": 11,
    }
    assert waiting_kwargs == {
        "state": state,
        "trigger_queue": trigger_queue,
        "scan_interval_seconds": 22,
        "customer_wait_timeout_hours": 33,
    }
    assert mention_kwargs["trigger_queue"] is trigger_queue
    assert mention_kwargs["poll_interval_seconds"] == 55
    assert mention_kwargs["tracker"] is app[MENTION_TRACKER_KEY]
    assert trigger_args == (trigger_queue, manager, state)


@pytest.mark.asyncio
async def test_shutdown_cancels_background_tasks_and_waits_manager() -> None:
    app = web.Application()
    manager = MagicMock()
    manager.active_count = 2
    manager.wait_all = AsyncMock()

    register_background_lifecycle(
        app=app,
        cfg=_cfg(),
        state_dir=MagicMock(),
        state=MagicMock(),
        trigger_queue=MagicMock(),
        manager=manager,
        trigger_consumer_fn=AsyncMock(),
        background_runners=BackgroundRunners(
            silence_waker=AsyncMock(),
            waiting_poller=AsyncMock(),
            mention_poller=AsyncMock(),
        ),
    )
    tasks = {key: FakeTask() for key in BACKGROUND_TASK_KEYS}
    app.update(tasks)

    await app.on_shutdown[-1](app)

    assert all(task.cancelled_flag for task in tasks.values())
    manager.wait_all.assert_awaited_once()
