"""RED contracts for active-session ownership and bounded retry recovery."""
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
from zd_agent.services.silence_waker import scan_once
from zd_agent.services.trigger_errors import TriggerDeferredError
from zd_agent.services.trigger_message_handler import handle_trigger_message
from zd_agent.services.trigger_state_machine import (
    apply_session_finished,
    apply_silence_window_expired,
)
from zd_agent.session.agent_session import AgentSession, SessionStatus
from zd_agent.trigger.decider import PreCheckVerdict
from zd_agent.trigger.state_store import TicketState, TicketStateTracker


_TICKET_ID = 3461
_NOW = datetime(2026, 7, 16, 7, 30, tzinfo=timezone.utc)


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


@pytest.mark.parametrize("source", ["zendesk_webhook", "mention"])
@pytest.mark.asyncio
async def test_new_webhook_does_not_reclassify_ticket_owned_by_active_session(
    tmp_path,
    source: str,
) -> None:
    """The running session owns new customer context through its freshness gate."""
    state_path = tmp_path / "tickets.json"
    state = TicketStateTracker(state_path)
    original = TicketState(
        state="processing",
        first_seen=_NOW - timedelta(minutes=10),
        last_customer_comment_id="customer-message-before-dispatch",
        last_comment_ts=_NOW - timedelta(minutes=2),
        last_verdict="substantial_issue",
        active_session_id="session-current",
        delivery_attempt_id="delivery-current",
    )
    state.set(_TICKET_ID, original)
    classify = AsyncMock(return_value=SimpleNamespace(
        verdict=PreCheckVerdict(
            verdict="insufficient_info",
            completeness=30,
            reason="would overwrite the active processing lifecycle",
        ),
    ))
    dispatch_verdict = AsyncMock()
    manager = MagicMock()
    manager.dispatch = AsyncMock()

    with pytest.raises(TriggerDeferredError) as exc_info:
        await handle_trigger_message(
            msg={"ticket_id": _TICKET_ID, "source": source},
            manager=manager,
            state=state,
            cfg=_cfg(),
            env=SimpleNamespace(openai_api_key="sk-test"),
            log_path=tmp_path / "trigger.jsonl",
            fetch_customer_messages_fn=AsyncMock(return_value=(
                [
                    {
                        "id": "customer-message-during-session",
                        "body": "One more detail while you investigate.",
                        "created_at": _NOW.isoformat(),
                        "content_type": "text",
                    },
                ],
                {
                    "id": _TICKET_ID,
                    "status": "open",
                    "assignee_id": None,
                    "via": {"channel": "native_messaging"},
                    "_zd_agent_conversation_events": [],
                },
            )),
            classify_ticket_messages_fn=classify,
            post_verdict_dispatch_fn=dispatch_verdict,
            escalate_fn=AsyncMock(),
            detect_human_takeover_fn=AsyncMock(return_value=(False, "")),
        )

    assert exc_info.value.reason == "active_session_processing"
    classify.assert_not_awaited()
    dispatch_verdict.assert_not_awaited()
    manager.dispatch.assert_not_awaited()
    persisted = TicketStateTracker(state_path).get(_TICKET_ID)
    assert persisted is not None
    assert persisted.state == "processing"
    assert persisted.active_session_id == "session-current"
    assert persisted.delivery_attempt_id == "delivery-current"
    assert persisted.last_customer_comment_id == "customer-message-before-dispatch"


@pytest.mark.asyncio
async def test_three_consecutive_agent_failures_exhaust_durable_retry_budget(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Repeated no-reply failures must stop waking the Agent after three attempts."""
    state_path = tmp_path / "tickets.json"
    tracker = TicketStateTracker(state_path)
    tracker.set(
        _TICKET_ID,
        TicketState(
            state="processing",
            first_seen=_NOW - timedelta(minutes=10),
            last_customer_comment_id="customer-message-1",
            last_comment_ts=_NOW - timedelta(minutes=1),
            last_verdict="substantial_issue",
            active_session_id="session-attempt-1",
            delivery_attempt_id="delivery-stable",
        ),
    )

    statuses = (
        SessionStatus.FAILED,
        SessionStatus.TIMEOUT,
        SessionStatus.FAILED,
    )
    for index, status in enumerate(statuses, start=1):
        attempt_time = _NOW + timedelta(seconds=index)
        session = AgentSession(
            ticket_id=_TICKET_ID,
            session_id=f"session-attempt-{index}",
        )
        session.status = status
        session.reply_sent = False

        # Reload on every attempt: the retry budget must survive restarts and
        # cannot live only in the current process or AgentSession object.
        tracker = TicketStateTracker(state_path)
        assert apply_session_finished(tracker, session, now=attempt_time) is True
        tracker = TicketStateTracker(state_path)

        if index < len(statuses):
            retryable = tracker.get(_TICKET_ID)
            assert retryable is not None
            assert retryable.state == "in_silence_window"
            transition = apply_silence_window_expired(
                tracker,
                _TICKET_ID,
                now=attempt_time + timedelta(seconds=1),
            )
            assert transition is not None
            assert transition.queue_source == "silence_waker"
            processing = tracker.get(_TICKET_ID)
            assert processing is not None
            processing.active_session_id = f"session-attempt-{index + 1}"
            tracker.set(_TICKET_ID, processing)

    terminal = TicketStateTracker(state_path).get(_TICKET_ID)
    assert terminal is not None
    assert terminal.state == "escalated_to_human"
    assert terminal.escalated_reason == "agent_retry_exhausted"
    assert terminal.active_session_id == ""
    assert terminal.delivery_attempt_id == "delivery-stable"

    monkeypatch.setattr(
        "zd_agent.services.silence_waker._now",
        lambda: _NOW + timedelta(hours=1),
    )
    queue: asyncio.Queue[dict] = asyncio.Queue()
    assert await scan_once(
        state=TicketStateTracker(state_path),
        trigger_queue=queue,
    ) == 0
    assert queue.empty()


@pytest.mark.asyncio
async def test_unique_mention_is_deferred_instead_of_dropped_during_cooldown(
    tmp_path,
) -> None:
    state = TicketStateTracker(tmp_path / "tickets.json")
    state.set(
        _TICKET_ID,
        TicketState(
            state="processed",
            first_seen=_NOW - timedelta(minutes=10),
            last_agent_finish_ts=datetime.now(timezone.utc),
        ),
    )
    manager = MagicMock(dispatch=AsyncMock())

    with pytest.raises(TriggerDeferredError) as exc_info:
        await handle_trigger_message(
            msg={
                "ticket_id": _TICKET_ID,
                "source": "mention",
                "comment_id": "internal-comment-2",
                "hint": "[来自 @mention 的请求]\nPlease re-check the latest logs.",
            },
            manager=manager,
            state=state,
            cfg=_cfg(),
            env=SimpleNamespace(openai_api_key="sk-test"),
            log_path=tmp_path / "trigger.jsonl",
        )

    assert exc_info.value.reason == "post_agent_cooldown"
    assert 0 < exc_info.value.delay_seconds <= 30
    manager.dispatch.assert_not_awaited()
