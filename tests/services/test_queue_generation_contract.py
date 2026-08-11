"""Contracts for stale silence events and ownerless processing prevention."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from zd_agent.config import AppConfig, TriggerDeciderConfig, ZendeskConfig
from zd_agent.services.silence_waker import scan_once
from zd_agent.services.trigger_message_handler import handle_trigger_message
from zd_agent.trigger.state_store import TicketState, TicketStateTracker


TICKET_ID = 3461
NOW = datetime(2026, 7, 16, 8, 0, tzinfo=timezone.utc)


def _cfg() -> AppConfig:
    cfg = AppConfig()
    cfg.zendesk = ZendeskConfig(
        subdomain="x",
        email="x@y.test",
        api_token="token",
        min_ticket_id=0,
        agent_user_id=45433304089364,
    )
    cfg.trigger_decider = TriggerDeciderConfig(
        llm_model="gpt-test",
        silence_minutes_default=0,
        post_agent_cooldown_seconds=180,
    )
    return cfg


def _expired_state(
    tmp_path,
    *,
    verdict: str = "substantial_issue",
) -> TicketStateTracker:
    tracker = TicketStateTracker(tmp_path / "tickets.json")
    tracker.set(
        TICKET_ID,
        TicketState(
            state="in_silence_window",
            first_seen=NOW - timedelta(minutes=10),
            silence_window_end=NOW - timedelta(seconds=1),
            last_verdict=verdict,
            last_customer_comment_id="m1",
            delivery_attempt_id="delivery-stable",
        ),
    )
    return tracker


@pytest.mark.asyncio
async def test_failed_enqueue_does_not_advance_silence_state(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _expired_state(tmp_path)
    queue = MagicMock()
    queue.put = AsyncMock(return_value=False)
    monkeypatch.setattr("zd_agent.services.silence_waker._now", lambda: NOW)

    assert await scan_once(state=tracker, trigger_queue=queue) == 0

    persisted = TicketStateTracker(tracker.path).get(TICKET_ID)
    assert persisted is not None
    assert persisted.state == "in_silence_window"
    assert persisted.active_session_id == ""


@pytest.mark.asyncio
async def test_stale_silence_event_is_noop_after_state_generation_changes(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _expired_state(tmp_path, verdict="insufficient_info")
    queue: asyncio.Queue[dict] = asyncio.Queue()
    monkeypatch.setattr("zd_agent.services.silence_waker._now", lambda: NOW)
    assert await scan_once(state=tracker, trigger_queue=queue) == 1
    message = queue.get_nowait()
    assert message["expected_state_revision"] >= 1

    # A newer customer event/state transition supersedes the delayed ask.
    current = tracker.get(TICKET_ID)
    assert current is not None
    current.state = "processed"
    current.last_customer_comment_id = "m2"
    tracker.set(TICKET_ID, current)

    manager = MagicMock(dispatch=AsyncMock())
    result = await handle_trigger_message(
        msg=message,
        manager=manager,
        state=tracker,
        cfg=_cfg(),
        log_path=tmp_path / "trigger.jsonl",
        fetch_customer_messages_fn=AsyncMock(),
    )

    assert result.action == "stale_queue_event"
    manager.dispatch.assert_not_awaited()


@pytest.mark.asyncio
async def test_waker_with_no_customer_message_never_leaves_ownerless_processing(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _expired_state(tmp_path)
    queue: asyncio.Queue[dict] = asyncio.Queue()
    monkeypatch.setattr("zd_agent.services.silence_waker._now", lambda: NOW)
    assert await scan_once(state=tracker, trigger_queue=queue) == 1
    message = queue.get_nowait()
    manager = MagicMock(dispatch=AsyncMock())

    result = await handle_trigger_message(
        msg=message,
        manager=manager,
        state=tracker,
        cfg=_cfg(),
        log_path=tmp_path / "trigger.jsonl",
        fetch_customer_messages_fn=AsyncMock(return_value=(
            [],
            {
                "id": TICKET_ID,
                "status": "new",
                "assignee_id": None,
                "via": {"channel": "native_messaging"},
                "_zd_agent_conversation_events": [],
            },
        )),
        human_takeover_fn=AsyncMock(),
    )

    assert result.action == "no_customer_message"
    manager.dispatch.assert_not_awaited()
    persisted = TicketStateTracker(tracker.path).get(TICKET_ID)
    assert persisted is not None
    assert persisted.state == "in_silence_window"
    assert persisted.active_session_id == ""


@pytest.mark.asyncio
async def test_legacy_silence_ask_row_dispatches_v2_without_old_nudge(
    tmp_path,
) -> None:
    """A delayed ask cannot answer an older generation after new customer input."""
    tracker = _expired_state(tmp_path, verdict="insufficient_info")
    pending = tracker.get(TICKET_ID)
    assert pending is not None
    fetch_latest = AsyncMock(return_value=(
        [
            {
                "id": "m1",
                "body": "The original report was incomplete.",
                "created_at": "2026-07-16T07:55:00Z",
                "content_type": "text",
            },
            {
                "id": "m2",
                "body": "Here is the missing workflow run ID: run-123.",
                "created_at": "2026-07-16T07:59:30Z",
                "content_type": "text",
            },
        ],
        {
            "id": TICKET_ID,
            "status": "open",
            "assignee_id": None,
            "via": {"channel": "native_messaging"},
            "_zd_agent_conversation_events": [],
        },
    ))
    manager = MagicMock(dispatch=AsyncMock())
    result = await handle_trigger_message(
        msg={
            "ticket_id": TICKET_ID,
            "source": "silence_expired_ask",
            "expected_state_revision": pending.state_revision,
            "expected_silence_window_end": (
                pending.silence_window_end.isoformat()
                if pending.silence_window_end else ""
            ),
            "delivery_attempt_id": pending.delivery_attempt_id,
        },
        manager=manager,
        state=tracker,
        cfg=_cfg(),
        log_path=tmp_path / "trigger.jsonl",
        fetch_customer_messages_fn=fetch_latest,
        human_takeover_fn=AsyncMock(),
        detect_human_takeover_fn=AsyncMock(return_value=(False, "")),
    )

    assert result.action == "direct_dispatch"
    fetch_latest.assert_awaited_once_with(TICKET_ID)
    manager.dispatch.assert_awaited_once()


@pytest.mark.asyncio
async def test_silence_event_stops_when_generation_changes_during_ticket_refresh(
    tmp_path,
) -> None:
    """A delayed event cannot dispatch a verdict after losing its generation."""
    tracker = _expired_state(tmp_path, verdict="insufficient_info")
    pending = tracker.get(TICKET_ID)
    assert pending is not None

    async def fetch_and_supersede(_ticket_id: int):
        superseding = tracker.get(TICKET_ID)
        assert superseding is not None
        superseding.state = "processed"
        superseding.last_customer_comment_id = "m2"
        tracker.set(TICKET_ID, superseding)
        return ([
            {
                "id": "m1",
                "body": "The original report was incomplete.",
                "created_at": "2026-07-16T07:55:00Z",
                "content_type": "text",
            },
            {
                "id": "m2",
                "body": "Here is the missing workflow run ID: run-123.",
                "created_at": "2026-07-16T07:59:30Z",
                "content_type": "text",
            },
        ], {
            "id": TICKET_ID,
            "status": "open",
            "assignee_id": None,
            "via": {"channel": "native_messaging"},
            "_zd_agent_conversation_events": [],
        })

    result = await handle_trigger_message(
        msg={
            "ticket_id": TICKET_ID,
            "source": "silence_expired_ask",
            "expected_state_revision": pending.state_revision,
            "expected_silence_window_end": (
                pending.silence_window_end.isoformat()
                if pending.silence_window_end else ""
            ),
            "delivery_attempt_id": pending.delivery_attempt_id,
        },
        manager=MagicMock(dispatch=AsyncMock()),
        state=tracker,
        cfg=_cfg(),
        log_path=tmp_path / "trigger.jsonl",
        fetch_customer_messages_fn=fetch_and_supersede,
        human_takeover_fn=AsyncMock(),
        detect_human_takeover_fn=AsyncMock(return_value=(False, "")),
    )

    assert result.action == "stale_queue_event"


@pytest.mark.asyncio
async def test_silence_expired_ask_honors_human_takeover_before_nudge(
    tmp_path,
) -> None:
    """Human ownership discovered live must suppress the scheduled customer ask."""
    tracker = _expired_state(tmp_path, verdict="insufficient_info")
    pending = tracker.get(TICKET_ID)
    assert pending is not None
    human_takeover = AsyncMock()

    result = await handle_trigger_message(
        msg={
            "ticket_id": TICKET_ID,
            "source": "silence_expired_ask",
            "expected_state_revision": pending.state_revision,
            "expected_silence_window_end": (
                pending.silence_window_end.isoformat()
                if pending.silence_window_end else ""
            ),
        },
        manager=MagicMock(dispatch=AsyncMock()),
        state=tracker,
        cfg=_cfg(),
        log_path=tmp_path / "trigger.jsonl",
        fetch_customer_messages_fn=AsyncMock(return_value=(
            [
                {
                    "id": "m1",
                    "body": "Please ask a teammate to take this case.",
                    "created_at": "2026-07-16T07:55:00Z",
                    "content_type": "text",
                },
            ],
            {
                "id": TICKET_ID,
                "status": "open",
                "assignee_id": 987654,
                "via": {"channel": "native_messaging"},
                "_zd_agent_conversation_events": [],
            },
        )),
        human_takeover_fn=human_takeover,
        detect_human_takeover_fn=AsyncMock(
            return_value=(True, "ticket assigned to a human agent"),
        ),
    )

    assert result.action == "human_takeover"
    human_takeover.assert_awaited_once()


@pytest.mark.asyncio
async def test_silence_expired_ask_waits_for_pending_form_before_nudge(
    tmp_path,
) -> None:
    """A live blocking form is an upstream readiness gate for a delayed ask."""
    tracker = _expired_state(tmp_path, verdict="insufficient_info")
    pending = tracker.get(TICKET_ID)
    assert pending is not None

    result = await handle_trigger_message(
        msg={
            "ticket_id": TICKET_ID,
            "source": "silence_expired_ask",
            "expected_state_revision": pending.state_revision,
            "expected_silence_window_end": (
                pending.silence_window_end.isoformat()
                if pending.silence_window_end else ""
            ),
        },
        manager=MagicMock(dispatch=AsyncMock()),
        state=tracker,
        cfg=_cfg(),
        log_path=tmp_path / "trigger.jsonl",
        fetch_customer_messages_fn=AsyncMock(return_value=(
            [
                {
                    "id": "m1",
                    "body": "I need help with my workspace.",
                    "created_at": "2026-07-16T07:55:00Z",
                    "content_type": "text",
                },
            ],
            {
                "id": TICKET_ID,
                "status": "new",
                "assignee_id": None,
                "via": {"channel": "native_messaging"},
                "_zd_agent_conversation_events": [
                    {
                        "id": "answer-bot-form-1",
                        "created_at": "2026-07-16T07:59:00Z",
                        "author": {"type": "business"},
                        "content": {
                            "type": "form",
                            "block_chat_input": True,
                            "submitted": False,
                            "fields": [
                                {
                                    "type": "TEXT",
                                    "name": "dataCapture.systemField.requester.email",
                                    "label": "Email",
                                },
                            ],
                        },
                    },
                ],
            },
        )),
        human_takeover_fn=AsyncMock(),
        detect_human_takeover_fn=AsyncMock(return_value=(False, "")),
    )

    assert result.action == "waiting_for_form"
    persisted = TicketStateTracker(tracker.path).get(TICKET_ID)
    assert persisted is not None
    assert persisted.state == "waiting_for_form"


@pytest.mark.asyncio
async def test_legacy_silence_ask_never_creates_a_new_legacy_intent(
    tmp_path,
) -> None:
    """Historical delayed rows are upgraded to v2 before any customer write."""
    tracker = _expired_state(tmp_path, verdict="insufficient_info")
    pending = tracker.get(TICKET_ID)
    assert pending is not None

    manager = MagicMock(dispatch=AsyncMock())

    result = await handle_trigger_message(
        msg={
            "ticket_id": TICKET_ID,
            "source": "silence_expired_ask",
            "expected_state_revision": pending.state_revision,
            "expected_silence_window_end": pending.silence_window_end.isoformat(),
            "delivery_attempt_id": pending.delivery_attempt_id,
            "_queue_key": "silence-ask-generation-1",
        },
        manager=manager,
        state=tracker,
        cfg=_cfg(),
        log_path=tmp_path / "trigger.jsonl",
        fetch_customer_messages_fn=AsyncMock(return_value=([
            {
                "id": "m1",
                "body": "The original report was incomplete.",
                "created_at": "2026-07-16T07:55:00Z",
                "content_type": "text",
            },
        ], {
            "id": TICKET_ID,
            "status": "new",
            "assignee_id": None,
            "via": {"channel": "native_messaging"},
            "_zd_agent_conversation_events": [],
        })),
        human_takeover_fn=AsyncMock(),
        detect_human_takeover_fn=AsyncMock(return_value=(False, "")),
    )

    assert result.action == "direct_dispatch"
    manager.dispatch.assert_awaited_once()


@pytest.mark.asyncio
async def test_startup_recovery_empty_messages_clear_dead_session_owner(
    tmp_path,
) -> None:
    tracker = TicketStateTracker(tmp_path / "tickets.json")
    tracker.set(
        TICKET_ID,
        TicketState(
            state="processing",
            first_seen=NOW - timedelta(minutes=10),
            active_session_id="dead-session",
            last_verdict="substantial_issue",
            delivery_attempt_id="delivery-stable",
        ),
    )

    result = await handle_trigger_message(
        msg={
            "ticket_id": TICKET_ID,
            "source": "startup_recovery",
            "_queue_key": "startup-recovery:3461",
            "delivery_attempt_id": "delivery-stable",
        },
        manager=MagicMock(dispatch=AsyncMock()),
        state=tracker,
        cfg=_cfg(),
        log_path=tmp_path / "trigger.jsonl",
        fetch_customer_messages_fn=AsyncMock(return_value=([], {
            "id": TICKET_ID,
            "status": "new",
            "assignee_id": None,
            "via": {"channel": "native_messaging"},
            "_zd_agent_conversation_events": [],
        })),
        human_takeover_fn=AsyncMock(),
    )

    assert result.action == "startup_recovery_waiting"
    retryable = tracker.get(TICKET_ID)
    assert retryable is not None
    assert retryable.state == "in_silence_window"
    assert retryable.active_session_id == ""


@pytest.mark.parametrize("new_terminal_state", ["processed", "abandoned"])
@pytest.mark.asyncio
async def test_silence_waker_revalidates_generation_after_live_fetch(
    tmp_path,
    new_terminal_state: str,
) -> None:
    """A state transition during live fetch invalidates the queued dispatch."""
    tracker = _expired_state(tmp_path)
    pending = tracker.get(TICKET_ID)
    assert pending is not None
    fetch_started = asyncio.Event()
    allow_fetch_to_finish = asyncio.Event()

    async def fetch_latest(_ticket_id: int) -> tuple[list[dict], dict]:
        assert _ticket_id == TICKET_ID
        fetch_started.set()
        await allow_fetch_to_finish.wait()
        return (
            [
                {
                    "id": "m1",
                    "body": "The original issue details.",
                    "created_at": "2026-07-16T07:55:00Z",
                    "content_type": "text",
                },
            ],
            {
                "id": TICKET_ID,
                "status": "open",
                "assignee_id": None,
                "via": {"channel": "native_messaging"},
                "_zd_agent_conversation_events": [],
            },
        )

    manager = MagicMock()
    manager.dispatch = AsyncMock(
        return_value=SimpleNamespace(session_id="stale-dispatch"),
    )
    task = asyncio.create_task(handle_trigger_message(
        msg={
            "ticket_id": TICKET_ID,
            "source": "silence_waker",
            "expected_state_revision": pending.state_revision,
            "expected_silence_window_end": (
                pending.silence_window_end.isoformat()
                if pending.silence_window_end else ""
            ),
            "delivery_attempt_id": pending.delivery_attempt_id,
        },
        manager=manager,
        state=tracker,
        cfg=_cfg(),
        log_path=tmp_path / "trigger.jsonl",
        fetch_customer_messages_fn=fetch_latest,
        human_takeover_fn=AsyncMock(),
        detect_human_takeover_fn=AsyncMock(return_value=(False, "")),
    ))

    await asyncio.wait_for(fetch_started.wait(), timeout=1)
    latest = tracker.get(TICKET_ID)
    assert latest is not None
    latest.state = new_terminal_state
    latest.silence_window_end = None
    tracker.set(TICKET_ID, latest)
    terminal_revision = latest.state_revision
    allow_fetch_to_finish.set()
    await asyncio.wait_for(task, timeout=1)

    manager.dispatch.assert_not_awaited()
    persisted = TicketStateTracker(tracker.path).get(TICKET_ID)
    assert persisted is not None
    assert persisted.state == new_terminal_state
    assert persisted.state_revision == terminal_revision
