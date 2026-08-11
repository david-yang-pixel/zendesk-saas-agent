"""Crash-recovery contracts for the durable trigger pipeline."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from aiohttp import web

from zd_agent.config import AppConfig
from zd_agent.services.app_bootstrap import prepare_saas_app_runtime
from zd_agent.services.durable_trigger_queue import DurableTriggerQueue
from zd_agent.services.silence_waker import scan_once
from zd_agent.services.trigger_consumer import (
    _own_dead_lettered_trigger,
    run_trigger_consumer,
)
from zd_agent.trigger.state_store import TicketState, TicketStateTracker


@pytest.mark.asyncio
async def test_startup_recovery_uses_a_queue_key_distinct_from_delivery_id(
    tmp_path,
) -> None:
    """A live pre-crash queue lease must not suppress startup recovery."""
    now = [1_000.0]
    queue = DurableTriggerQueue(
        tmp_path / "trigger_queue.sqlite3",
        lease_seconds=1_800,
        now_fn=lambda: now[0],
    )
    delivery_id = "zendesk:3461:customer-message-1"
    assert (
        await queue.put(
            {
                "source": "zendesk_webhook",
                "ticket_id": 3461,
                "delivery_attempt_id": delivery_id,
                "_queue_key": delivery_id,
            }
        )
        is True
    )
    original_claim = await queue.get()

    restarted = DurableTriggerQueue(
        queue.path,
        lease_seconds=1_800,
        now_fn=lambda: now[0],
    )
    assert (
        restarted.ensure_processing_recovery(
            3461,
            delivery_attempt_id=delivery_id,
        )
        is True
    )

    recovered = await restarted.get()
    assert recovered["source"] == "startup_recovery"
    assert recovered["_queue_key"].startswith("startup-recovery:3461:")
    assert recovered["delivery_attempt_id"] == delivery_id
    assert recovered["_queue_event_id"] != original_claim["_queue_event_id"]


def test_bootstrap_does_not_report_recovery_that_was_not_queued(tmp_path) -> None:
    cfg = AppConfig()
    cfg.resources.base_dir = str(tmp_path)
    cfg.resources.state_dir = ".state"
    state_dir = tmp_path / ".state"
    state_dir.mkdir()
    state = TicketStateTracker(state_dir / "tickets.json")
    state.set(
        3461,
        TicketState(
            state="processing",
            first_seen=datetime.now(timezone.utc),
            delivery_attempt_id="delivery-stable",
        ),
    )

    with (
        patch.object(
            DurableTriggerQueue,
            "ensure_processing_recovery",
            return_value=False,
        ),
        patch("zd_agent.services.app_bootstrap.logger.warning") as warning,
    ):
        prepare_saas_app_runtime(
            app=web.Application(),
            cfg=cfg,
            manager=MagicMock(),
        )

    successful_recovery_logs = [
        call
        for call in warning.call_args_list
        if call.args and call.args[0] == "processing_sessions_recovered"
    ]
    assert successful_recovery_logs == []


@pytest.mark.asyncio
async def test_stale_dead_letter_generation_never_escalates_current_silence_state(
    tmp_path,
) -> None:
    state = TicketStateTracker(tmp_path / "tickets.json")
    now = datetime.now(timezone.utc)
    state.set(
        3461,
        TicketState(
            state="in_silence_window",
            first_seen=now - timedelta(minutes=10),
            silence_window_end=now + timedelta(minutes=1),
            last_verdict="substantial_issue",
        ),
    )
    original_revision = state.get(3461).state_revision
    current = state.get(3461)
    current.last_customer_comment_id = "new-customer-generation"
    state.set(3461, current)
    await _own_dead_lettered_trigger(
        message={
            "ticket_id": 3461,
            "source": "silence_waker",
            "expected_state_revision": original_revision,
            "_queue_attempts": 5,
        },
        state=state,
    )

    assert state.get(3461).state == "in_silence_window"


@pytest.mark.asyncio
async def test_malformed_dead_letter_does_not_stop_trigger_consumer(tmp_path) -> None:
    handled_valid = asyncio.Event()

    class Queue:
        schedules_available_at = True

        def __init__(self) -> None:
            self.messages = [
                {
                    "ticket_id": "not-an-int",
                    "source": "zendesk_webhook",
                    "_queue_attempts": 4,
                },
                {"ticket_id": 3461, "source": "zendesk_webhook"},
            ]

        async def get(self) -> dict:
            if self.messages:
                return self.messages.pop(0)
            await asyncio.Event().wait()

        async def retry(self, message: dict, _error: str) -> bool:
            return message["ticket_id"] != "not-an-int"

        async def ack(self, _message: dict) -> bool:
            return True

    async def handle_message(*, msg: dict, **_kwargs) -> None:
        if msg["ticket_id"] == "not-an-int":
            raise RuntimeError("malformed terminal event")
        handled_valid.set()

    task = asyncio.create_task(
        run_trigger_consumer(
            Queue(),
            MagicMock(),
            TicketStateTracker(tmp_path / "tickets.json"),
            cfg=MagicMock(),
            log_path=tmp_path / "trigger.jsonl",
            handle_message_fn=handle_message,
        )
    )
    await asyncio.wait_for(handled_valid.wait(), timeout=1)
    assert task.done() is False
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_silence_waker_revives_same_generation_dead_letter(tmp_path) -> None:
    state = TicketStateTracker(tmp_path / "tickets.json")
    now = datetime.now(timezone.utc)
    state.set(
        3461,
        TicketState(
            state="in_silence_window",
            first_seen=now - timedelta(minutes=10),
            silence_window_end=now - timedelta(seconds=1),
            last_verdict="substantial_issue",
            delivery_attempt_id="delivery-stable",
        ),
    )
    queue = DurableTriggerQueue(
        tmp_path / "trigger_queue.sqlite3",
        max_attempts=1,
        retry_base_seconds=0,
    )

    assert await scan_once(state=state, trigger_queue=queue) == 1
    failed = await queue.get()
    assert await queue.retry(failed, "consumer crashed before escalation") is False

    restarted = DurableTriggerQueue(
        queue.path,
        max_attempts=1,
        retry_base_seconds=0,
    )
    assert await scan_once(state=state, trigger_queue=restarted) == 1
    recovered = await restarted.get()
    assert recovered["_queue_key"] == failed["_queue_key"]
    assert recovered["_queue_attempts"] == 0
    assert recovered["expected_state_revision"] == state.get(3461).state_revision


@pytest.mark.asyncio
async def test_lost_lease_mutations_are_fenced_by_claim_token(tmp_path) -> None:
    now = [1_000.0]
    path = tmp_path / "trigger_queue.sqlite3"
    stale_owner = DurableTriggerQueue(
        path,
        lease_seconds=10,
        retry_base_seconds=0,
        now_fn=lambda: now[0],
    )
    await stale_owner.put({"ticket_id": 3461, "source": "zendesk_webhook"})
    stale_claim = await stale_owner.get()

    now[0] = 1_011.0
    current_owner = DurableTriggerQueue(
        path,
        lease_seconds=10,
        retry_base_seconds=0,
        now_fn=lambda: now[0],
    )
    current_claim = await current_owner.get()

    assert await stale_owner.ack(stale_claim) is False
    assert await stale_owner.release(stale_claim) is False
    assert await stale_owner.retry(stale_claim, "stale worker") is None
    assert await stale_owner.defer(stale_claim, 0, "stale worker") is None
    assert await current_owner.release(current_claim) is True
    assert await current_owner.pending_count() == 1
    assert await current_owner.list_dead_letters() == []
