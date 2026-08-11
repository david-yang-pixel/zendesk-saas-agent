from __future__ import annotations

import asyncio
import sqlite3
import stat
import threading
from pathlib import Path

import pytest

from zd_agent.services.durable_trigger_queue import DurableTriggerQueue


@pytest.mark.asyncio
async def test_cancelled_get_releases_claim_committed_by_worker_thread(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cancellation cannot strand a row after SQLite committed its lease."""
    queue = DurableTriggerQueue(
        tmp_path / "trigger_queue.sqlite3",
        lease_seconds=30 * 60,
    )
    await queue.put({"ticket_id": 1001, "source": "zendesk_webhook"})
    claim_committed = threading.Event()
    allow_claim_return = threading.Event()
    original_claim_ready = queue._store.claim_ready

    def claim_then_pause() -> tuple[dict | None, float | None]:
        result = original_claim_ready()
        if result[0] is not None:
            claim_committed.set()
            assert allow_claim_return.wait(timeout=5)
        return result

    monkeypatch.setattr(queue._store, "claim_ready", claim_then_pause)
    get_task = asyncio.create_task(queue.get())
    assert await asyncio.to_thread(claim_committed.wait, 1)

    get_task.cancel()
    allow_claim_return.set()
    with pytest.raises(asyncio.CancelledError):
        await get_task

    monkeypatch.setattr(queue._store, "claim_ready", original_claim_ready)
    recovered = await asyncio.wait_for(queue.get(), timeout=1)
    assert recovered["ticket_id"] == 1001
    assert recovered["_queue_attempts"] == 0
    await queue.ack(recovered)


@pytest.mark.asyncio
async def test_double_cancelled_get_releases_claim_committed_by_worker_thread(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Repeated lifecycle cancellation cannot interrupt claim cleanup."""
    queue = DurableTriggerQueue(
        tmp_path / "trigger_queue.sqlite3",
        lease_seconds=30 * 60,
    )
    await queue.put({"ticket_id": 1003, "source": "zendesk_webhook"})
    claim_committed = threading.Event()
    allow_claim_return = threading.Event()
    original_claim_ready = queue._store.claim_ready

    def claim_then_pause() -> tuple[dict | None, float | None]:
        result = original_claim_ready()
        if result[0] is not None:
            claim_committed.set()
            assert allow_claim_return.wait(timeout=5)
        return result

    monkeypatch.setattr(queue._store, "claim_ready", claim_then_pause)
    get_task = asyncio.create_task(queue.get())
    assert await asyncio.to_thread(claim_committed.wait, 1)

    get_task.cancel()
    await asyncio.sleep(0)
    get_task.cancel()
    allow_claim_return.set()
    with pytest.raises(asyncio.CancelledError):
        await get_task

    monkeypatch.setattr(queue._store, "claim_ready", original_claim_ready)
    recovered = await asyncio.wait_for(queue.get(), timeout=1)
    assert recovered["ticket_id"] == 1003
    assert recovered["_queue_attempts"] == 0
    await queue.ack(recovered)


@pytest.mark.asyncio
async def test_waiting_queue_observes_insert_from_second_instance(
    tmp_path: Path,
) -> None:
    """The process-local wake event cannot be the only cross-worker signal."""
    path = tmp_path / "trigger_queue.sqlite3"
    waiting_queue = DurableTriggerQueue(path, poll_interval_seconds=0.05)
    producer_queue = DurableTriggerQueue(path, poll_interval_seconds=0.05)

    waiting = asyncio.create_task(waiting_queue.get())
    await asyncio.sleep(0.05)
    await producer_queue.put({"ticket_id": 1002, "source": "zendesk_webhook"})

    message = await asyncio.wait_for(waiting, timeout=0.5)
    assert message["ticket_id"] == 1002
    await waiting_queue.ack(message)


@pytest.mark.asyncio
async def test_pending_event_survives_queue_recreation(tmp_path: Path) -> None:
    path = tmp_path / "trigger_queue.sqlite3"
    first = DurableTriggerQueue(path)
    await first.put({"ticket_id": 1001, "source": "zendesk_webhook"})

    second = DurableTriggerQueue(path)
    message = await second.get()

    assert message["ticket_id"] == 1001
    await second.ack(message)
    assert await second.pending_count() == 0


@pytest.mark.asyncio
async def test_ready_event_is_not_blocked_by_delayed_event(tmp_path: Path) -> None:
    queue = DurableTriggerQueue(tmp_path / "trigger_queue.sqlite3")
    await queue.put(
        {
            "ticket_id": 1001,
            "source": "zendesk_webhook",
            "available_at": 9_999_999_999.0,
        }
    )
    await queue.put({"ticket_id": 1002, "source": "mention"})

    message = await queue.get()

    assert message["ticket_id"] == 1002


@pytest.mark.asyncio
async def test_failed_event_is_requeued_then_dead_lettered(tmp_path: Path) -> None:
    queue = DurableTriggerQueue(
        tmp_path / "trigger_queue.sqlite3",
        max_attempts=2,
        retry_base_seconds=0,
    )
    await queue.put({"ticket_id": 1001, "source": "zendesk_webhook"})

    first = await queue.get()
    assert await queue.retry(first, "temporary failure") is True

    second = await queue.get()
    assert second["_queue_attempts"] == 1
    assert await queue.retry(second, "permanent failure") is False

    assert await queue.pending_count() == 0
    dead_letters = await queue.list_dead_letters()
    assert len(dead_letters) == 1
    assert dead_letters[0]["ticket_id"] == 1001


@pytest.mark.asyncio
async def test_release_requeues_without_incrementing_attempts(tmp_path: Path) -> None:
    queue = DurableTriggerQueue(
        tmp_path / "trigger_queue.sqlite3",
        max_attempts=2,
        retry_base_seconds=0,
    )
    await queue.put({"ticket_id": 1001, "source": "zendesk_webhook"})
    claimed = await queue.get()

    await queue.release(claimed)
    recovered = await queue.get()

    assert recovered["ticket_id"] == 1001
    assert recovered["_queue_attempts"] == 0
    await queue.ack(recovered)


@pytest.mark.asyncio
async def test_queue_key_makes_put_idempotent(tmp_path: Path) -> None:
    queue = DurableTriggerQueue(tmp_path / "trigger_queue.sqlite3")
    message = {
        "ticket_id": 1001,
        "source": "zendesk_webhook",
        "_queue_key": "zendesk:1001:abc",
    }

    assert await queue.put(message) is True
    assert await queue.put(message) is False
    assert await queue.pending_count() == 1


@pytest.mark.asyncio
async def test_concurrent_consumers_claim_distinct_events(tmp_path: Path) -> None:
    queue = DurableTriggerQueue(tmp_path / "trigger_queue.sqlite3")
    await queue.put({"ticket_id": 1001, "source": "zendesk_webhook"})
    await queue.put({"ticket_id": 1002, "source": "zendesk_webhook"})

    first, second = await asyncio.gather(queue.get(), queue.get())

    assert {first["ticket_id"], second["ticket_id"]} == {1001, 1002}
    assert first["_queue_event_id"] != second["_queue_event_id"]
    await asyncio.gather(queue.ack(first), queue.ack(second))
    assert await queue.pending_count() == 0


@pytest.mark.asyncio
async def test_processing_recovery_event_is_idempotent(tmp_path: Path) -> None:
    queue = DurableTriggerQueue(tmp_path / "trigger_queue.sqlite3")

    assert queue.ensure_processing_recovery(1001) is True
    assert queue.ensure_processing_recovery(1001) is False
    assert await queue.pending_count() == 1


@pytest.mark.asyncio
async def test_processing_recovery_revives_dead_letter(tmp_path: Path) -> None:
    queue = DurableTriggerQueue(
        tmp_path / "trigger_queue.sqlite3",
        max_attempts=1,
        retry_base_seconds=0,
    )
    assert queue.ensure_processing_recovery(1001) is True
    failed = await queue.get()
    assert await queue.retry(failed, "startup failed") is False

    assert queue.ensure_processing_recovery(1001) is True
    recovered = await queue.get()

    assert recovered["_queue_attempts"] == 0
    await queue.ack(recovered)


@pytest.mark.asyncio
async def test_processing_recovery_is_generation_bound_and_revives_reconciled(
    tmp_path: Path,
) -> None:
    queue = DurableTriggerQueue(
        tmp_path / "trigger_queue.sqlite3",
        max_attempts=1,
        retry_base_seconds=0,
    )
    assert queue.ensure_processing_recovery(
        1001,
        delivery_attempt_id="delivery-generation-1",
    )
    first = await queue.get()
    assert await queue.retry(first, "old recovery exhausted") is False
    dead_letter = (await queue.list_dead_letters())[0]
    assert await queue.resolve_dead_letter(dead_letter) is True

    assert queue.ensure_processing_recovery(
        1001,
        delivery_attempt_id="delivery-generation-2",
    )
    second = await queue.get()
    assert second["delivery_attempt_id"] == "delivery-generation-2"
    assert second["_queue_key"] != first["_queue_key"]
    await queue.ack(second)

    assert queue.ensure_processing_recovery(
        1001,
        delivery_attempt_id="delivery-generation-1",
    )
    revived = await queue.get()
    assert revived["_queue_key"] == first["_queue_key"]
    assert revived["_queue_attempts"] == 0
    await queue.ack(revived)


@pytest.mark.asyncio
async def test_materialization_deferrals_are_bounded_and_dead_lettered(
    tmp_path: Path,
) -> None:
    queue = DurableTriggerQueue(
        tmp_path / "trigger_queue.sqlite3",
        max_materialization_defers=3,
        retry_base_seconds=0,
    )
    await queue.put({"ticket_id": 1001, "source": "messaging_webhook"})

    first = await queue.get()
    assert first["_queue_deferrals"] == 0
    assert await queue.defer(first, 0, "ticket_not_materialized") is True

    second = await queue.get()
    assert second["_queue_deferrals"] == 1
    assert await queue.defer(second, 0, "ticket_not_materialized") is True

    third = await queue.get()
    assert third["_queue_deferrals"] == 2
    assert await queue.defer(third, 0, "ticket_not_materialized") is False

    assert await queue.pending_count() == 0
    [dead] = await queue.list_dead_letters()
    assert dead["ticket_id"] == 1001
    assert "ticket_not_materialized" in dead["_queue_error"]


@pytest.mark.asyncio
async def test_claim_lease_prevents_second_instance_from_stealing_active_work(
    tmp_path: Path,
) -> None:
    now = [1000.0]
    path = tmp_path / "trigger_queue.sqlite3"
    first = DurableTriggerQueue(path, lease_seconds=10, now_fn=lambda: now[0])
    await first.put({"ticket_id": 1001, "source": "zendesk_webhook"})
    first_claim = await first.get()

    second = DurableTriggerQueue(path, lease_seconds=10, now_fn=lambda: now[0])
    await second.put({"ticket_id": 1002, "source": "zendesk_webhook"})
    other_message = await second.get()

    assert other_message["ticket_id"] == 1002
    assert first_claim["_queue_claim_token"]


@pytest.mark.asyncio
async def test_expired_claim_is_recovered_and_stale_owner_cannot_ack_it(
    tmp_path: Path,
) -> None:
    now = [1000.0]
    path = tmp_path / "trigger_queue.sqlite3"
    first = DurableTriggerQueue(path, lease_seconds=10, now_fn=lambda: now[0])
    await first.put({"ticket_id": 1001, "source": "zendesk_webhook"})
    stale_claim = await first.get()

    now[0] = 1011.0
    second = DurableTriggerQueue(path, lease_seconds=10, now_fn=lambda: now[0])
    recovered_claim = await second.get()
    await first.ack(stale_claim)
    await second.release(recovered_claim)

    assert recovered_claim["_queue_claim_token"] != stale_claim["_queue_claim_token"]
    assert await second.pending_count() == 1


@pytest.mark.asyncio
async def test_waiting_consumer_wakes_when_processing_lease_expires(
    tmp_path: Path,
) -> None:
    path = tmp_path / "trigger_queue.sqlite3"
    first = DurableTriggerQueue(path, lease_seconds=1)
    await first.put({"ticket_id": 1001, "source": "zendesk_webhook"})
    await first.get()

    second = DurableTriggerQueue(path, lease_seconds=1)
    recovered = await asyncio.wait_for(second.get(), timeout=1.5)

    assert recovered["ticket_id"] == 1001
    await second.ack(recovered)


@pytest.mark.asyncio
async def test_corrupt_payload_is_dead_lettered_without_blocking_next_event(
    tmp_path: Path,
) -> None:
    path = tmp_path / "trigger_queue.sqlite3"
    queue = DurableTriggerQueue(path)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO trigger_events("
            "event_key,payload,status,available_at,attempts,created_at,updated_at"
            ") VALUES (?,?,?,?,?,?,?)",
            ("corrupt", "{broken", "pending", 0, 0, 0, 0),
        )
    await queue.put({"ticket_id": 1002, "source": "zendesk_webhook"})

    message = await queue.get()
    dead_letters = await queue.list_dead_letters()

    assert message["ticket_id"] == 1002
    assert dead_letters[0]["_queue_key"] == "corrupt"
    assert dead_letters[0]["_queue_corrupt_payload"] is True


@pytest.mark.asyncio
async def test_non_utf8_payload_is_dead_lettered_without_stopping_queue(
    tmp_path: Path,
) -> None:
    path = tmp_path / "trigger_queue.sqlite3"
    queue = DurableTriggerQueue(path)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO trigger_events("
            "event_key,payload,status,available_at,attempts,created_at,updated_at"
            ") VALUES (?,?,?,?,?,?,?)",
            ("binary-corrupt", sqlite3.Binary(b"\xff"), "pending", 0, 0, 0, 0),
        )
    await queue.put({"ticket_id": 1002, "source": "zendesk_webhook"})

    message = await queue.get()
    dead_letters = await queue.list_dead_letters()

    assert message["ticket_id"] == 1002
    assert dead_letters[0]["_queue_key"] == "binary-corrupt"
    assert dead_letters[0]["_queue_corrupt_payload"] is True


def test_queue_database_and_sidecars_use_owner_only_permissions(tmp_path: Path) -> None:
    path = tmp_path / "state" / "trigger_queue.sqlite3"
    queue = DurableTriggerQueue(path)
    connection = queue._store.connect()
    try:
        connection.execute("BEGIN IMMEDIATE")
        existing_paths = [
            candidate
            for candidate in (path, Path(f"{path}-wal"), Path(f"{path}-shm"))
            if candidate.exists()
        ]
        queue._store.restrict_file_permissions()
        assert len(existing_paths) == 3
        assert all(
            stat.S_IMODE(item.stat().st_mode) == 0o600 for item in existing_paths
        )
    finally:
        connection.rollback()
        connection.close()

    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
