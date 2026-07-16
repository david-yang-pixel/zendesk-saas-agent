"""RED regression contracts for silence-waker recovery and readiness."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from zd_agent.config import (
    AppConfig,
    PublicReplyConfig,
    TriggerDeciderConfig,
    ZendeskConfig,
)
from zd_agent.services.direct_dispatch import handle_direct_dispatch_source
from zd_agent.services.silence_waker import scan_once
from zd_agent.services.trigger_message_handler import handle_trigger_message
from zd_agent.services.trigger_state_machine import apply_session_finished
from zd_agent.session.agent_session import AgentSession, SessionStatus
from zd_agent.trigger.state_store import TicketState, TicketStateTracker


_TICKET_ID = 3461
_NOW = datetime(2026, 7, 16, 4, 30, tzinfo=timezone.utc)


def _cfg() -> AppConfig:
    cfg = AppConfig()
    cfg.public_reply = PublicReplyConfig(enabled=False)
    cfg.zendesk = ZendeskConfig(
        subdomain="x",
        email="x@y.com",
        api_token="t",
        min_ticket_id=0,
        agent_user_id=45433304089364,
    )
    cfg.trigger_decider = TriggerDeciderConfig(
        llm_model="gpt-test",
        silence_minutes_default=0,
        post_agent_cooldown_seconds=180,
    )
    return cfg


def _expired_substantial_state() -> TicketState:
    return TicketState(
        state="in_silence_window",
        first_seen=_NOW - timedelta(minutes=10),
        last_customer_comment_id="m1",
        last_comment_ts=_NOW - timedelta(minutes=2),
        silence_window_end=_NOW - timedelta(seconds=1),
        last_verdict="substantial_issue",
    )


def _pending_answer_bot_form() -> dict:
    return {
        "id": "bot-form-1",
        "created_at": "2026-07-16T04:29:55Z",
        "author": {"type": "business", "display_name": "Dify"},
        "content": {
            "type": "form",
            "block_chat_input": True,
            "submitted": False,
            "fields": [
                {
                    "type": "TEXT",
                    "name": "dataCapture.ticketField.41021162892948",
                    "label": "Workspace ID",
                },
            ],
        },
    }


@pytest.mark.asyncio
async def test_failed_silence_waker_dispatch_restores_retryable_state(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Queue consumption failure must not leave a processing zombie."""
    state = TicketStateTracker(tmp_path / "tickets.json")
    state.set(_TICKET_ID, _expired_substantial_state())
    queue: asyncio.Queue[dict] = asyncio.Queue()
    monkeypatch.setattr("zd_agent.services.silence_waker._now", lambda: _NOW)

    assert await scan_once(state=state, trigger_queue=queue) == 1
    queued = queue.get_nowait()
    assert state.get(_TICKET_ID).state == "in_silence_window"

    manager = MagicMock()
    manager.dispatch = AsyncMock(side_effect=RuntimeError("worker unavailable"))

    with pytest.raises(RuntimeError, match="worker unavailable"):
        await handle_direct_dispatch_source(
            ticket_id=_TICKET_ID,
            msg=queued,
            manager=manager,
            state=state,
            log_path=tmp_path / "trigger.jsonl",
            cfg=_cfg(),
            now=_NOW,
        )

    recovered = state.get(_TICKET_ID)
    assert recovered is not None
    assert recovered.state == "in_silence_window"
    assert recovered.silence_window_end is not None
    assert recovered.active_session_id == ""


@pytest.mark.asyncio
async def test_silence_waker_checks_latest_ticket_for_pending_form_before_dispatch(
    tmp_path,
) -> None:
    """A pending Answer Bot form remains an upstream gate for a waker run."""
    state = TicketStateTracker(tmp_path / "tickets.json")
    state.set(_TICKET_ID, _expired_substantial_state())
    pending = state.get(_TICKET_ID)
    assert pending is not None

    manager = MagicMock()
    manager.dispatch = AsyncMock(
        return_value=AgentSession(ticket_id=_TICKET_ID, session_id="too-early"),
    )
    fetch_latest = AsyncMock(return_value=(
        [
            {
                "id": "m1",
                "body": "I cannot view existing knowledge documents.",
                "created_at": "2026-07-16T04:28:00Z",
                "content_type": "text",
            },
        ],
        {
            "id": _TICKET_ID,
            "status": "new",
            "assignee_id": None,
            "via": {"channel": "native_messaging"},
            "_zd_agent_conversation_events": [_pending_answer_bot_form()],
        },
    ))

    result = await handle_trigger_message(
        msg={
            "ticket_id": _TICKET_ID,
            "source": "silence_waker",
            "expected_state_revision": pending.state_revision,
            "expected_silence_window_end": (
                pending.silence_window_end.isoformat()
                if pending.silence_window_end else ""
            ),
        },
        manager=manager,
        state=state,
        cfg=_cfg(),
        env=SimpleNamespace(openai_api_key="sk-test"),
        log_path=tmp_path / "trigger.jsonl",
        fetch_customer_messages_fn=fetch_latest,
        classify_ticket_messages_fn=AsyncMock(),
        post_verdict_dispatch_fn=AsyncMock(),
        escalate_fn=AsyncMock(),
        detect_human_takeover_fn=AsyncMock(return_value=(False, "")),
    )

    assert result.action == "waiting_for_form"
    fetch_latest.assert_awaited_once_with(_TICKET_ID)
    manager.dispatch.assert_not_awaited()
    waiting = state.get(_TICKET_ID)
    assert waiting is not None
    assert waiting.state == "waiting_for_form"
    assert waiting.last_verdict == "waiting_for_form"


@pytest.mark.parametrize("last_verdict", ["", "greeting", "insufficient_info"])
@pytest.mark.asyncio
async def test_deferred_mention_always_wakes_agent_after_sixty_seconds(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    last_verdict: str,
) -> None:
    """A bounded freshness defer retries the Agent, never a customer ask."""
    state = TicketStateTracker(tmp_path / f"tickets-{last_verdict or 'empty'}.json")
    session = AgentSession(
        ticket_id=_TICKET_ID,
        session_id="deferred-mention",
        trigger_type="webhook-mention",
    )
    session.status = SessionStatus.DEFERRED
    session.reply_sent = False
    state.set(
        _TICKET_ID,
        TicketState(
            state="processing",
            first_seen=_NOW - timedelta(minutes=10),
            last_customer_comment_id="m2",
            last_comment_ts=_NOW - timedelta(seconds=5),
            last_verdict=last_verdict,
            active_session_id=session.session_id,
        ),
    )

    assert apply_session_finished(state, session, now=_NOW) is True
    deferred = state.get(_TICKET_ID)
    assert deferred is not None
    assert deferred.state == "in_silence_window"
    assert deferred.silence_window_end == _NOW + timedelta(seconds=60)

    monkeypatch.setattr(
        "zd_agent.services.silence_waker._now",
        lambda: _NOW + timedelta(seconds=60),
    )
    queue: asyncio.Queue[dict] = asyncio.Queue()
    assert await scan_once(state=state, trigger_queue=queue) == 1

    retry = queue.get_nowait()
    assert retry["source"] == "silence_waker"
    assert retry["ticket_id"] == _TICKET_ID
    assert retry["expected_state_revision"] == state.get(_TICKET_ID).state_revision
    assert state.get(_TICKET_ID).state == "in_silence_window"
