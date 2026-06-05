"""Tests for trigger queue consumer service."""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from zd_agent.services.trigger_actions import (
    handle_silence_expired_ask_action,
    post_verdict_dispatch_action,
    set_escalated_to_human_action,
)
from zd_agent.services.trigger_consumer import (
    run_saas_trigger_consumer,
    run_trigger_consumer,
)


@pytest.mark.asyncio
async def test_run_trigger_consumer_delegates_queue_messages(tmp_path) -> None:
    queue: asyncio.Queue = asyncio.Queue()
    manager = MagicMock()
    state = MagicMock()
    cfg = MagicMock()
    env = MagicMock()
    log_path = tmp_path / "trigger_log.jsonl"
    handled = asyncio.Event()
    calls: list[dict] = []

    async def fake_handle_message(**kwargs):
        calls.append(kwargs)
        handled.set()

    await queue.put({"ticket_id": 2808, "source": "zendesk_webhook"})
    task = asyncio.create_task(
        run_trigger_consumer(
            queue,
            manager,
            state,
            cfg=cfg,
            env=env,
            log_path=log_path,
            handle_message_fn=fake_handle_message,
            silence_expired_ask_fn=MagicMock(),
            post_verdict_dispatch_fn=MagicMock(),
            escalate_fn=MagicMock(),
        )
    )

    await asyncio.wait_for(handled.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert calls[0]["msg"] == {"ticket_id": 2808, "source": "zendesk_webhook"}
    assert calls[0]["manager"] is manager
    assert calls[0]["state"] is state
    assert calls[0]["cfg"] is cfg
    assert calls[0]["env"] is env
    assert calls[0]["log_path"] == log_path


@pytest.mark.asyncio
async def test_run_saas_trigger_consumer_injects_default_actions(monkeypatch) -> None:
    calls: list[dict] = []

    async def fake_run_trigger_consumer(*args, **kwargs):
        calls.append({"args": args, "kwargs": kwargs})

    monkeypatch.setattr(
        "zd_agent.services.trigger_consumer.run_trigger_consumer",
        fake_run_trigger_consumer,
    )
    queue = MagicMock()
    manager = MagicMock()
    state = MagicMock()
    cfg = MagicMock()

    await run_saas_trigger_consumer(queue, manager, state, cfg=cfg)

    assert calls[0]["args"] == (queue, manager, state)
    assert calls[0]["kwargs"]["cfg"] is cfg
    assert (
        calls[0]["kwargs"]["silence_expired_ask_fn"]
        is handle_silence_expired_ask_action
    )
    assert (
        calls[0]["kwargs"]["post_verdict_dispatch_fn"]
        is post_verdict_dispatch_action
    )
    assert calls[0]["kwargs"]["escalate_fn"] is set_escalated_to_human_action
