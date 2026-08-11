"""Tests for trigger queue consumer service."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from zd_agent.casework.domain.enums import (
    OwnershipAction,
    OwnershipState,
    ReasonCode,
)
from zd_agent.services.casework_runtime_failures import (
    RuntimeFailureOwnershipReceipt,
)
from zd_agent.services.trigger_actions import (
    handle_silence_expired_ask_action,
    post_verdict_dispatch_action,
    set_escalated_to_human_action,
)
from zd_agent.services.trigger_consumer import (
    reconcile_dead_lettered_triggers,
    run_saas_trigger_consumer,
    run_trigger_consumer,
)
from zd_agent.services.durable_trigger_queue import DurableTriggerQueue
from zd_agent.services.trigger_errors import TriggerDeferredError
from zd_agent.trigger.state_store import TicketState, TicketStateTracker


def _runtime_failure_receipt() -> RuntimeFailureOwnershipReceipt:
    return RuntimeFailureOwnershipReceipt(
        reference="casework-effect:queue",
        ownership_action=OwnershipAction.SUPPORT,
        reason_code=ReasonCode.AUTOMATION_RETRY_EXHAUSTED,
        ownership_state=OwnershipState.CONFIRMED,
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
async def test_run_trigger_consumer_waits_for_delayed_message(tmp_path, monkeypatch) -> None:
    queue: asyncio.Queue = asyncio.Queue()
    manager = MagicMock()
    state = MagicMock()
    cfg = MagicMock()
    env = MagicMock()
    log_path = tmp_path / "trigger_log.jsonl"
    handled = asyncio.Event()
    wait = AsyncMock()

    async def fake_handle_message(**kwargs):
        handled.set()

    monkeypatch.setattr(
        "zd_agent.services.trigger_consumer.wait_for_trigger_availability",
        wait,
    )
    await queue.put({
        "ticket_id": 2808,
        "source": "messaging_webhook",
        "available_at": 1030.0,
    })
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

    wait.assert_awaited_once()
    assert wait.await_args.args[0]["available_at"] == 1030.0


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


@pytest.mark.asyncio
async def test_run_trigger_consumer_retries_failed_durable_message(tmp_path) -> None:
    handled = asyncio.Event()

    class FakeDurableQueue:
        schedules_available_at = True

        def __init__(self) -> None:
            self.retried: list[tuple[dict, str]] = []

        async def get(self) -> dict:
            await asyncio.sleep(0)
            return {"ticket_id": 2808, "source": "zendesk_webhook"}

        async def retry(self, message: dict, error: str) -> bool:
            self.retried.append((message, error))
            handled.set()
            return True

        async def ack(self, message: dict) -> None:
            raise AssertionError("failed messages must not be acknowledged")

    async def fail_message(**_kwargs):
        raise RuntimeError("temporary downstream failure")

    queue = FakeDurableQueue()
    task = asyncio.create_task(
        run_trigger_consumer(
            queue,
            MagicMock(),
            MagicMock(),
            cfg=MagicMock(),
            env=MagicMock(),
            log_path=tmp_path / "trigger_log.jsonl",
            handle_message_fn=fail_message,
            silence_expired_ask_fn=MagicMock(),
            post_verdict_dispatch_fn=MagicMock(),
            escalate_fn=MagicMock(),
        )
    )

    await asyncio.wait_for(handled.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert len(queue.retried) == 1
    assert "temporary downstream failure" in queue.retried[0][1]


@pytest.mark.asyncio
async def test_run_trigger_consumer_releases_cancelled_durable_message(tmp_path) -> None:
    entered = asyncio.Event()
    released = asyncio.Event()

    class FakeDurableQueue:
        schedules_available_at = True

        def __init__(self) -> None:
            self.released: list[dict] = []

        async def get(self) -> dict:
            return {
                "ticket_id": 2808,
                "source": "zendesk_webhook",
                "_queue_attempts": 4,
            }

        async def release(self, message: dict) -> None:
            self.released.append(message)
            released.set()

        async def retry(self, _message: dict, _error: str) -> bool:
            raise AssertionError("normal cancellation must not consume a retry")

        async def ack(self, _message: dict) -> None:
            raise AssertionError("cancelled messages must not be acknowledged")

    async def block_until_cancelled(**_kwargs) -> None:
        entered.set()
        await asyncio.Event().wait()

    queue = FakeDurableQueue()
    task = asyncio.create_task(
        run_trigger_consumer(
            queue,
            MagicMock(),
            MagicMock(),
            cfg=MagicMock(),
            env=MagicMock(),
            log_path=tmp_path / "trigger_log.jsonl",
            handle_message_fn=block_until_cancelled,
            silence_expired_ask_fn=MagicMock(),
            post_verdict_dispatch_fn=MagicMock(),
            escalate_fn=MagicMock(),
        )
    )

    await asyncio.wait_for(entered.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    await asyncio.wait_for(released.wait(), timeout=1)
    assert len(queue.released) == 1
    assert queue.released[0]["_queue_attempts"] == 4


@pytest.mark.asyncio
async def test_deferred_durable_message_remains_pending_without_retry_consumption(
    tmp_path,
    monkeypatch,
) -> None:
    now = [1000.0]
    queue = DurableTriggerQueue(
        tmp_path / "trigger_queue.sqlite3",
        now_fn=lambda: now[0],
    )
    await queue.put({"ticket_id": 2808, "source": "zendesk_webhook"})
    deferred = asyncio.Event()
    original_defer = queue.defer

    async def observed_defer(message, delay_seconds, reason):
        await original_defer(message, delay_seconds, reason)
        deferred.set()

    monkeypatch.setattr(queue, "defer", observed_defer)

    async def defer_message(**_kwargs):
        raise TriggerDeferredError("post_agent_cooldown", delay_seconds=160)

    task = asyncio.create_task(
        run_trigger_consumer(
            queue,
            MagicMock(),
            MagicMock(),
            cfg=MagicMock(),
            env=MagicMock(),
            log_path=tmp_path / "trigger_log.jsonl",
            handle_message_fn=defer_message,
            silence_expired_ask_fn=MagicMock(),
            post_verdict_dispatch_fn=MagicMock(),
            escalate_fn=MagicMock(),
        )
    )

    await asyncio.wait_for(deferred.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert await queue.pending_count() == 1
    now[0] = 1161.0
    recovered = await queue.get()
    assert recovered["_queue_attempts"] == 0
    await queue.ack(recovered)


@pytest.mark.asyncio
async def test_dead_lettered_silence_event_creates_typed_v2_owner(tmp_path) -> None:
    """A terminal queue failure cannot strand an expired ticket forever."""
    terminal = asyncio.Event()
    now = datetime(2026, 7, 16, 8, 30, tzinfo=timezone.utc)
    state = TicketStateTracker(tmp_path / "tickets.json")
    state.set(
        3461,
        TicketState(
            state="in_silence_window",
            first_seen=now - timedelta(minutes=10),
            silence_window_end=now - timedelta(seconds=1),
            last_verdict="agent_retry",
            delivery_attempt_id="delivery-stable",
        ),
    )

    class DeadLetterQueue:
        schedules_available_at = True

        def __init__(self) -> None:
            self.delivered = False

        async def get(self) -> dict:
            if self.delivered:
                await asyncio.Event().wait()
            self.delivered = True
            return {
                "ticket_id": 3461,
                "source": "silence_waker",
                "delivery_attempt_id": "delivery-stable",
                "_queue_attempts": 4,
            }

        async def retry(self, _message: dict, _error: str) -> bool:
            return False

        async def ack(self, _message: dict) -> None:
            raise AssertionError("failed messages must not be acknowledged")

    async def own_failure(**_kwargs) -> RuntimeFailureOwnershipReceipt:
        terminal.set()
        return _runtime_failure_receipt()

    async def fail_message(**_kwargs) -> None:
        raise RuntimeError("permanent trigger failure")

    task = asyncio.create_task(
        run_trigger_consumer(
            DeadLetterQueue(),
            MagicMock(),
            state,
            cfg=MagicMock(),
            env=MagicMock(),
            log_path=tmp_path / "trigger_log.jsonl",
            handle_message_fn=fail_message,
            silence_expired_ask_fn=MagicMock(),
            post_verdict_dispatch_fn=MagicMock(),
            escalate_fn=AsyncMock(),
            runtime_failure_ownership_fn=own_failure,
        ),
    )

    await asyncio.wait_for(terminal.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    persisted = TicketStateTracker(state.path).get(3461)
    assert persisted is not None
    assert persisted.state == "owned"
    assert persisted.ownership_action == "support"
    assert persisted.ownership_reason == "automation_retry_exhausted"
    assert persisted.ownership_effect_reference == "casework-effect:queue"
    assert persisted.delivery_attempt_id == "delivery-stable"


@pytest.mark.asyncio
async def test_dead_letter_reconciliation_completes_crash_gap(tmp_path) -> None:
    """Rows dead-lettered before the consumer's escalation survive restart."""
    state = TicketStateTracker(tmp_path / "tickets.json")
    state.set(
        12345,
        TicketState(state="waiting_for_issue", first_seen=datetime.now(timezone.utc)),
    )

    class Queue:
        async def list_dead_letters(self, *, limit: int) -> list[dict]:
            assert limit == 100
            return [{"ticket_id": 12345, "source": "zendesk_webhook", "_queue_attempts": 3}]

    escalated = AsyncMock()
    own_failure = AsyncMock(return_value=_runtime_failure_receipt())
    count = await reconcile_dead_lettered_triggers(
        queue=Queue(),
        state=state,
        log_path=tmp_path / "trigger.jsonl",
        escalate_fn=escalated,
        runtime_failure_ownership_fn=own_failure,
    )
    assert count == 1
    escalated.assert_not_awaited()
    own_failure.assert_awaited_once()
    persisted = state.get(12345)
    assert persisted is not None and persisted.state == "owned"
