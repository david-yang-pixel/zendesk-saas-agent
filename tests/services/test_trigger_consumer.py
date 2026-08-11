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
from zd_agent.services.external_ownership import record_human_takeover
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
    human_takeover = MagicMock()
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
            log_path=log_path,
            handle_message_fn=fake_handle_message,
            human_takeover_fn=human_takeover,
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
    assert calls[0]["log_path"] == log_path
    assert calls[0]["human_takeover_fn"] is human_takeover


@pytest.mark.asyncio
async def test_consumer_recovers_when_queue_claim_temporarily_fails(tmp_path) -> None:
    handled = asyncio.Event()

    class FlakyQueue:
        schedules_available_at = False

        def __init__(self) -> None:
            self.calls = 0

        async def get(self) -> dict:
            self.calls += 1
            if self.calls == 1:
                raise OSError("temporary sqlite read failure")
            if self.calls == 2:
                return {"ticket_id": 2809, "source": "zendesk_webhook"}
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

    async def handle(**_kwargs) -> None:
        handled.set()

    queue = FlakyQueue()
    consumer = asyncio.create_task(
        run_trigger_consumer(
            queue,
            MagicMock(),
            MagicMock(),
            cfg=MagicMock(),
            log_path=tmp_path / "trigger_log.jsonl",
            handle_message_fn=handle,
        )
    )

    await asyncio.wait_for(handled.wait(), timeout=1)
    consumer.cancel()
    with pytest.raises(asyncio.CancelledError):
        await consumer

    assert queue.calls >= 2


@pytest.mark.asyncio
async def test_run_trigger_consumer_waits_for_delayed_message(
    tmp_path, monkeypatch
) -> None:
    queue: asyncio.Queue = asyncio.Queue()
    manager = MagicMock()
    state = MagicMock()
    cfg = MagicMock()
    log_path = tmp_path / "trigger_log.jsonl"
    handled = asyncio.Event()
    wait = AsyncMock()

    async def fake_handle_message(**kwargs):
        handled.set()

    monkeypatch.setattr(
        "zd_agent.services.trigger_consumer.wait_for_trigger_availability",
        wait,
    )
    await queue.put(
        {
            "ticket_id": 2808,
            "source": "messaging_webhook",
            "available_at": 1030.0,
        }
    )
    task = asyncio.create_task(
        run_trigger_consumer(
            queue,
            manager,
            state,
            cfg=cfg,
            log_path=log_path,
            handle_message_fn=fake_handle_message,
            human_takeover_fn=MagicMock(),
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
    assert calls[0]["kwargs"]["human_takeover_fn"] is record_human_takeover


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
            log_path=tmp_path / "trigger_log.jsonl",
            handle_message_fn=fail_message,
            human_takeover_fn=MagicMock(),
        )
    )

    await asyncio.wait_for(handled.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert len(queue.retried) == 1
    assert "temporary downstream failure" in queue.retried[0][1]


@pytest.mark.asyncio
async def test_durable_retry_persists_original_state_generation_fence(tmp_path) -> None:
    queue = DurableTriggerQueue(
        tmp_path / "trigger_queue.sqlite3",
        max_attempts=3,
        retry_base_seconds=0,
    )
    state = TicketStateTracker(tmp_path / "tickets.json")
    state.set(
        2808,
        TicketState(state="in_silence_window", first_seen=datetime.now(timezone.utc)),
    )
    original = state.get(2808)
    assert original is not None
    await queue.put({"ticket_id": 2808, "source": "zendesk_webhook"})
    second_attempt = asyncio.Event()
    calls = 0
    observed: dict = {}

    async def fail_once(**kwargs) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("temporary")
        observed.update(kwargs["msg"])
        second_attempt.set()

    task = asyncio.create_task(
        run_trigger_consumer(
            queue,
            MagicMock(),
            state,
            cfg=MagicMock(),
            log_path=tmp_path / "trigger_log.jsonl",
            handle_message_fn=fail_once,
            human_takeover_fn=MagicMock(),
        )
    )
    await asyncio.wait_for(second_attempt.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert observed["_state_fence_version"] == 1
    assert observed["expected_state_revision"] == original.state_revision
    assert observed["expected_state"] == "in_silence_window"


@pytest.mark.asyncio
async def test_state_fence_read_failure_retries_claim_without_killing_consumer(
    tmp_path,
    monkeypatch,
) -> None:
    queue = DurableTriggerQueue(
        tmp_path / "trigger_queue.sqlite3",
        max_attempts=3,
        retry_base_seconds=0,
    )
    tracker = TicketStateTracker(tmp_path / "tickets.json")
    tracker.set(
        2818,
        TicketState(
            state="in_silence_window",
            first_seen=datetime.now(timezone.utc),
        ),
    )

    class FlakyState:
        path = tracker.path

        def __init__(self) -> None:
            self.calls = 0

        def get(self, ticket_id):
            self.calls += 1
            if self.calls == 1:
                raise OSError("transient state read failed")
            return tracker.get(ticket_id)

        def __getattr__(self, name):
            return getattr(tracker, name)

    state = FlakyState()
    handled = asyncio.Event()
    acknowledged = asyncio.Event()
    real_ack = queue.ack

    async def observed_ack(message):
        result = await real_ack(message)
        if result:
            acknowledged.set()
        return result

    monkeypatch.setattr(queue, "ack", observed_ack)

    async def handle(**_kwargs) -> None:
        handled.set()

    await queue.put({"ticket_id": 2818, "source": "zendesk_webhook"})
    consumer = asyncio.create_task(
        run_trigger_consumer(
            queue,
            MagicMock(),
            state,
            cfg=MagicMock(),
            log_path=tmp_path / "trigger_log.jsonl",
            handle_message_fn=handle,
        )
    )

    await asyncio.wait_for(handled.wait(), timeout=1)
    await asyncio.wait_for(acknowledged.wait(), timeout=1)
    consumer.cancel()
    with pytest.raises(asyncio.CancelledError):
        await consumer

    assert state.calls >= 2
    assert await queue.pending_count() == 0


@pytest.mark.asyncio
async def test_consumer_retries_transient_ack_failure_and_keeps_running(
    tmp_path,
    monkeypatch,
) -> None:
    queue = DurableTriggerQueue(
        tmp_path / "trigger_queue.sqlite3",
        retry_base_seconds=0,
    )
    state = TicketStateTracker(tmp_path / "tickets.json")
    real_delete = queue._store.delete
    delete_calls = 0

    def flaky_delete(event_id: int, claim_token: str) -> bool:
        nonlocal delete_calls
        delete_calls += 1
        if delete_calls == 1:
            raise OSError("transient ack write failure")
        return real_delete(event_id, claim_token)

    monkeypatch.setattr(queue._store, "delete", flaky_delete)
    handled: list[int] = []
    second_handled = asyncio.Event()
    second_acknowledged = asyncio.Event()
    real_ack = queue.ack

    async def observed_ack(message):
        result = await real_ack(message)
        if result and int(message["ticket_id"]) == 2822:
            second_acknowledged.set()
        return result

    monkeypatch.setattr(queue, "ack", observed_ack)

    async def handle(**kwargs) -> None:
        handled.append(int(kwargs["msg"]["ticket_id"]))
        if len(handled) == 2:
            second_handled.set()

    await queue.put({"ticket_id": 2821, "source": "zendesk_webhook"})
    consumer = asyncio.create_task(
        run_trigger_consumer(
            queue,
            MagicMock(),
            state,
            cfg=MagicMock(),
            log_path=tmp_path / "trigger_log.jsonl",
            handle_message_fn=handle,
        ),
    )
    while delete_calls < 2:
        await asyncio.sleep(0.01)
    await queue.put({"ticket_id": 2822, "source": "zendesk_webhook"})
    await asyncio.wait_for(second_handled.wait(), timeout=1)
    await asyncio.wait_for(second_acknowledged.wait(), timeout=1)

    consumer.cancel()
    with pytest.raises(asyncio.CancelledError):
        await consumer
    assert handled == [2821, 2822]
    assert delete_calls >= 3


@pytest.mark.asyncio
async def test_consumer_retries_transient_retry_persistence_failure(
    tmp_path,
    monkeypatch,
) -> None:
    queue = DurableTriggerQueue(
        tmp_path / "trigger_queue.sqlite3",
        retry_base_seconds=0,
    )
    state = TicketStateTracker(tmp_path / "tickets.json")
    real_mark_failed = queue._store.mark_failed
    persistence_calls = 0
    handled = asyncio.Event()
    handler_calls = 0

    def flaky_mark_failed(*args, **kwargs):
        nonlocal persistence_calls
        persistence_calls += 1
        if persistence_calls == 1:
            raise OSError("transient retry write failure")
        return real_mark_failed(*args, **kwargs)

    async def handle(**_kwargs) -> None:
        nonlocal handler_calls
        handler_calls += 1
        if handler_calls == 1:
            raise RuntimeError("retry me")
        handled.set()

    monkeypatch.setattr(queue._store, "mark_failed", flaky_mark_failed)
    await queue.put({"ticket_id": 2823, "source": "zendesk_webhook"})
    consumer = asyncio.create_task(
        run_trigger_consumer(
            queue,
            MagicMock(),
            state,
            cfg=MagicMock(),
            log_path=tmp_path / "trigger_log.jsonl",
            handle_message_fn=handle,
        ),
    )
    await asyncio.wait_for(handled.wait(), timeout=1)

    consumer.cancel()
    with pytest.raises(asyncio.CancelledError):
        await consumer
    assert persistence_calls >= 2
    assert handler_calls == 2


@pytest.mark.asyncio
async def test_consumer_retries_transient_defer_persistence_failure(
    tmp_path,
    monkeypatch,
) -> None:
    queue = DurableTriggerQueue(
        tmp_path / "trigger_queue.sqlite3",
        retry_base_seconds=0,
    )
    state = TicketStateTracker(tmp_path / "tickets.json")
    real_reschedule = queue._store.reschedule
    persistence_calls = 0
    handled = asyncio.Event()
    handler_calls = 0

    def flaky_reschedule(*args, **kwargs):
        nonlocal persistence_calls
        persistence_calls += 1
        if persistence_calls == 1:
            raise OSError("transient defer write failure")
        return real_reschedule(*args, **kwargs)

    async def handle(**_kwargs) -> None:
        nonlocal handler_calls
        handler_calls += 1
        if handler_calls == 1:
            raise TriggerDeferredError(
                "wait_for_materialization",
                delay_seconds=0,
            )
        handled.set()

    monkeypatch.setattr(queue._store, "reschedule", flaky_reschedule)
    await queue.put({"ticket_id": 2824, "source": "zendesk_webhook"})
    consumer = asyncio.create_task(
        run_trigger_consumer(
            queue,
            MagicMock(),
            state,
            cfg=MagicMock(),
            log_path=tmp_path / "trigger_log.jsonl",
            handle_message_fn=handle,
        ),
    )
    await asyncio.wait_for(handled.wait(), timeout=1)

    consumer.cancel()
    with pytest.raises(asyncio.CancelledError):
        await consumer
    assert persistence_calls >= 2
    assert handler_calls == 2


@pytest.mark.asyncio
async def test_run_trigger_consumer_releases_cancelled_durable_message(
    tmp_path,
) -> None:
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
            log_path=tmp_path / "trigger_log.jsonl",
            handle_message_fn=block_until_cancelled,
            human_takeover_fn=MagicMock(),
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
async def test_double_cancelled_consumer_waits_for_durable_release(tmp_path) -> None:
    entered = asyncio.Event()
    release_started = asyncio.Event()
    allow_release = asyncio.Event()
    released = False

    class FakeDurableQueue:
        schedules_available_at = True

        async def get(self) -> dict:
            return {
                "ticket_id": 2820,
                "source": "zendesk_webhook",
                "_queue_attempts": 0,
            }

        async def release(self, _message: dict) -> bool:
            nonlocal released
            release_started.set()
            await allow_release.wait()
            released = True
            return True

    async def block(**_kwargs) -> None:
        entered.set()
        await asyncio.Event().wait()

    consumer = asyncio.create_task(
        run_trigger_consumer(
            FakeDurableQueue(),
            MagicMock(),
            MagicMock(),
            cfg=MagicMock(),
            log_path=tmp_path / "trigger_log.jsonl",
            handle_message_fn=block,
        )
    )
    await asyncio.wait_for(entered.wait(), timeout=1)

    consumer.cancel()
    await asyncio.wait_for(release_started.wait(), timeout=1)
    consumer.cancel()
    await asyncio.sleep(0)
    assert consumer.done() is False

    allow_release.set()
    with pytest.raises(asyncio.CancelledError):
        await consumer
    assert released is True


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
            log_path=tmp_path / "trigger_log.jsonl",
            handle_message_fn=defer_message,
            human_takeover_fn=MagicMock(),
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
            log_path=tmp_path / "trigger_log.jsonl",
            handle_message_fn=fail_message,
            human_takeover_fn=AsyncMock(),
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
    existing = state.get(12345)
    assert existing is not None

    class Queue:
        def __init__(self) -> None:
            self.resolved: list[dict] = []

        async def list_dead_letters(self, *, limit: int) -> list[dict]:
            assert limit == 100
            return [
                {
                    "ticket_id": 12345,
                    "source": "zendesk_webhook",
                    "_queue_attempts": 3,
                    "_state_fence_version": 1,
                    "expected_state_revision": existing.state_revision,
                    "expected_state": existing.state,
                    "expected_active_session_id": existing.active_session_id,
                    "expected_source_event_id": "",
                }
            ]

        async def resolve_dead_letter(self, message: dict) -> bool:
            self.resolved.append(message)
            return True

    queue = Queue()
    own_failure = AsyncMock(return_value=_runtime_failure_receipt())
    count = await reconcile_dead_lettered_triggers(
        queue=queue,
        state=state,
        runtime_failure_ownership_fn=own_failure,
    )
    assert count == 1
    assert len(queue.resolved) == 1
    own_failure.assert_awaited_once()
    persisted = state.get(12345)
    assert persisted is not None and persisted.state == "owned"


@pytest.mark.asyncio
async def test_dead_letter_reconciliation_does_not_starve_rows_after_first_page(
    tmp_path,
) -> None:
    queue = DurableTriggerQueue(
        tmp_path / "trigger_queue.sqlite3",
        max_attempts=1,
        retry_base_seconds=0,
    )
    for index in range(101):
        await queue.put(
            {
                "source": "poison",
                "_queue_key": f"poison:{index}",
            }
        )
        message = await queue.get()
        assert await queue.retry(message, "permanent") is False

    state = TicketStateTracker(tmp_path / "tickets.json")
    first = await reconcile_dead_lettered_triggers(queue=queue, state=state, limit=100)
    second = await reconcile_dead_lettered_triggers(queue=queue, state=state, limit=100)

    assert first == 100
    assert second == 1
    assert await queue.dead_letter_count() == 0
    assert await queue.list_dead_letters() == []


@pytest.mark.asyncio
async def test_deleted_state_dead_letter_is_resolved_without_blocking_next_row(
    tmp_path,
) -> None:
    state = TicketStateTracker(tmp_path / "tickets.json")
    state.set(
        2819,
        TicketState(
            state="in_silence_window",
            first_seen=datetime.now(timezone.utc),
        ),
    )
    current = state.get(2819)
    assert current is not None
    deleted = {
        "ticket_id": 9999,
        "source": "zendesk_webhook",
        "_queue_event_id": 1,
        "_state_fence_version": 1,
        "expected_state_revision": 7,
        "expected_state": "in_silence_window",
        "expected_active_session_id": "",
    }
    valid = {
        "ticket_id": 2819,
        "source": "zendesk_webhook",
        "delivery_attempt_id": "valid-dead-letter",
        "_queue_event_id": 2,
        "_queue_attempts": 3,
        "_state_fence_version": 1,
        "expected_state_revision": current.state_revision,
        "expected_state": current.state,
        "expected_active_session_id": current.active_session_id,
    }

    class Queue:
        def __init__(self) -> None:
            self.resolved: list[int] = []

        async def list_dead_letters(self, *, limit: int) -> list[dict]:
            return [deleted, valid]

        async def resolve_dead_letter(self, message: dict) -> bool:
            self.resolved.append(message["ticket_id"])
            return True

    queue = Queue()
    ownership = AsyncMock(return_value=_runtime_failure_receipt())

    reconciled = await reconcile_dead_lettered_triggers(
        queue=queue,
        state=state,
        runtime_failure_ownership_fn=ownership,
    )

    assert reconciled == 2
    assert queue.resolved == [9999, 2819]
    ownership.assert_awaited_once()
    persisted = state.get(2819)
    assert persisted is not None and persisted.state == "owned"


@pytest.mark.asyncio
async def test_dead_letter_effect_cannot_overwrite_newer_ticket_generation(
    tmp_path,
) -> None:
    state = TicketStateTracker(tmp_path / "tickets.json")
    state.set(
        12345,
        TicketState(
            state="in_silence_window",
            first_seen=datetime.now(timezone.utc),
            delivery_attempt_id="old-attempt",
        ),
    )
    old = state.get(12345)
    assert old is not None
    message = {
        "ticket_id": 12345,
        "source": "zendesk_webhook",
        "delivery_attempt_id": "old-attempt",
        "_state_fence_version": 1,
        "expected_state_revision": old.state_revision,
        "expected_state": old.state,
        "expected_active_session_id": old.active_session_id,
        "expected_source_event_id": old.last_customer_comment_id or "",
        "_queue_event_id": 1,
        "_queue_attempts": 3,
    }

    class Queue:
        async def list_dead_letters(self, *, limit: int) -> list[dict]:
            return [message]

        async def resolve_dead_letter(self, _message: dict) -> bool:
            return True

    entered = asyncio.Event()
    resume = asyncio.Event()

    async def delayed_ownership(**_kwargs) -> RuntimeFailureOwnershipReceipt:
        entered.set()
        await resume.wait()
        return _runtime_failure_receipt()

    reconciliation = asyncio.create_task(
        reconcile_dead_lettered_triggers(
            queue=Queue(),
            state=state,
            runtime_failure_ownership_fn=delayed_ownership,
        )
    )
    await asyncio.wait_for(entered.wait(), timeout=1)
    current = state.get(12345)
    assert current is not None
    current.state = "awaiting_customer"
    current.delivery_attempt_id = "new-attempt"
    state.set(12345, current)
    resume.set()

    assert await reconciliation == 1
    persisted = state.get(12345)
    assert persisted is not None
    assert persisted.state == "awaiting_customer"
    assert persisted.delivery_attempt_id == "new-attempt"
