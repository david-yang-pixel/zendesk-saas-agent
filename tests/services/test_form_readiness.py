"""Regression specifications for Zendesk Answer Bot form readiness (#3461).

The customer's first free-form message can arrive before Zendesk has finished
the Answer Bot data-capture flow.  Classification must wait for that form to be
submitted, while the later ``formResponse`` remains available as structured
profile context without becoming issue text or language evidence.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from zd_agent.config import (
    AppConfig,
    PublicReplyConfig,
    TriggerDeciderConfig,
    ZendeskConfig,
)
from zd_agent.services.ticket_message_parsing import (
    _messaging_customer_messages,
    extract_form_data_from_msgs,
)
from zd_agent.services.trigger_actions import post_verdict_dispatch_action
from zd_agent.services.trigger_message_handler import handle_trigger_message
from zd_agent.services.verdict_classification import classify_ticket_messages
from zd_agent.services.waiting_poller import scan_once
from zd_agent.tools.zendesk import _extract_reply_language_hint
from zd_agent.trigger.decider import PreCheckVerdict
from zd_agent.trigger.state_store import TicketState, TicketStateTracker


_TICKET_ID = 3461
_WORKSPACE_ID = "86628345-731d-4475-b681-5006f48a2287"


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


def _initial_issue_event() -> dict[str, Any]:
    return {
        "id": "m1",
        "created_at": "2026-07-16T04:15:22Z",
        "author": {
            "type": "end-user",
            "zen:support:user_id": 24680,
            "display_name": "Ellie",
        },
        "content": {
            "type": "text",
            "text": (
                "I cannot figure out how to update our knowledge documentation. "
                "I can create new knowledge, but cannot view existing documents."
            ),
        },
    }


def _pending_answer_bot_form_event() -> dict[str, Any]:
    return {
        "id": "bot-form-1",
        "created_at": "2026-07-16T04:15:30Z",
        "author": {
            "type": "business",
            "display_name": "Dify",
        },
        "content": {
            "type": "form",
            "block_chat_input": True,
            "submitted": False,
            "fields": [
                {
                    "type": "TEXT",
                    "name": "dataCapture.systemField.requester.name",
                    "label": "Name",
                },
                {
                    "type": "EMAIL",
                    "name": "dataCapture.systemField.requester.email",
                    "label": "Email",
                },
                {
                    "type": "TEXT",
                    "name": "dataCapture.ticketField.41021162892948",
                    "label": "Workspace ID",
                },
            ],
        },
    }


def _form_response_event(
    *,
    message_id: str = "m2",
    name: str = "Ellie",
    email: str = "ellie.blackman@atticus.tech",
    workspace_id: str = _WORKSPACE_ID,
) -> dict[str, Any]:
    return {
        "id": message_id,
        "created_at": "2026-07-16T04:18:53Z",
        "author": {
            "type": "end-user",
            "zen:support:user_id": 24680,
            "display_name": name,
        },
        "content": {
            "type": "formResponse",
            # #3461 did not require text_fallback for the values to exist.  In
            # particular Zendesk stores EMAIL values under field.email.
            "fields": [
                {
                    "type": "TEXT",
                    "name": "dataCapture.systemField.requester.name",
                    "label": "Name",
                    "text": name,
                },
                {
                    "type": "EMAIL",
                    "name": "dataCapture.systemField.requester.email",
                    "label": "Email",
                    "email": email,
                },
                {
                    "type": "TEXT",
                    "name": "dataCapture.ticketField.41021162892948",
                    "label": "Workspace ID",
                    "text": workspace_id,
                },
            ],
        },
    }


def _ticket_payload(events: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "success": True,
        "data": {
            "ticket": {
                "id": _TICKET_ID,
                "status": "new",
                "requester_id": 24680,
                "assignee_id": None,
                "via": {"channel": "native_messaging"},
            },
            "comments": [],
            "conversation_log": {"events": events},
        },
    }


def _substantial_verdict() -> PreCheckVerdict:
    return PreCheckVerdict(
        verdict="substantial_issue",
        completeness=90,
        reason="The initial free-form message states an actionable issue.",
    )


@pytest.mark.asyncio
async def test_pending_answer_bot_form_waits_without_classification_or_dispatch(
    tmp_path,
) -> None:
    """A blocked, unsubmitted form is an upstream readiness gate."""
    state = TicketStateTracker(tmp_path / "tickets.json")
    classify = AsyncMock(
        return_value=SimpleNamespace(verdict=_substantial_verdict()),
    )
    post_verdict = AsyncMock()
    mcp = AsyncMock(return_value=_ticket_payload([
        _initial_issue_event(),
        _pending_answer_bot_form_event(),
    ]))

    with patch("zd_agent.integrations.zendesk_mcp.call_zendesk_tool", mcp):
        result = await handle_trigger_message(
            msg={"ticket_id": _TICKET_ID, "source": "messaging_webhook"},
            manager=MagicMock(),
            state=state,
            cfg=_cfg(),
            env=SimpleNamespace(openai_api_key="sk-test"),
            log_path=tmp_path / "trigger.jsonl",
            classify_ticket_messages_fn=classify,
            post_verdict_dispatch_fn=post_verdict,
            escalate_fn=AsyncMock(),
            detect_human_takeover_fn=AsyncMock(return_value=(False, "")),
        )

    assert result.action == "waiting_for_form"
    waiting = state.get(_TICKET_ID)
    assert waiting is not None
    assert waiting.state == "waiting_for_form"
    assert waiting.last_customer_comment_id == "m1"
    classify.assert_not_awaited()
    post_verdict.assert_not_awaited()


@pytest.mark.asyncio
async def test_form_response_releases_waiting_form_and_allows_processing(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A response after the pending form wins and resumes normal processing."""
    from zd_agent.trigger import state_store

    # Keep this behavior test runnable in RED before the new state is registered.
    monkeypatch.setattr(
        state_store,
        "VALID_STATES",
        state_store.VALID_STATES | {"waiting_for_form"},
    )
    state = TicketStateTracker(tmp_path / "tickets.json")
    state.set(
        _TICKET_ID,
        TicketState(
            state="waiting_for_form",
            first_seen=datetime(2026, 7, 16, 4, 15, 22, tzinfo=timezone.utc),
            last_customer_comment_id="m1",
            last_comment_ts=datetime(2026, 7, 16, 4, 15, 22, tzinfo=timezone.utc),
        ),
    )
    classify = AsyncMock(
        return_value=SimpleNamespace(verdict=_substantial_verdict()),
    )
    mcp = AsyncMock(return_value=_ticket_payload([
        _initial_issue_event(),
        _pending_answer_bot_form_event(),
        _form_response_event(),
    ]))

    with patch("zd_agent.integrations.zendesk_mcp.call_zendesk_tool", mcp):
        result = await handle_trigger_message(
            msg={"ticket_id": _TICKET_ID, "source": "messaging_webhook"},
            manager=MagicMock(),
            state=state,
            cfg=_cfg(),
            env=SimpleNamespace(openai_api_key="sk-test"),
            log_path=tmp_path / "trigger.jsonl",
            classify_ticket_messages_fn=classify,
            post_verdict_dispatch_fn=post_verdict_dispatch_action,
            escalate_fn=AsyncMock(),
            detect_human_takeover_fn=AsyncMock(return_value=(False, "")),
        )

    assert result.action == "verdict_dispatched"
    classify.assert_awaited_once()
    resumed = state.get(_TICKET_ID)
    assert resumed is not None
    assert resumed.state == "in_silence_window"
    assert resumed.last_customer_comment_id == "m2"


def test_fields_only_form_response_preserves_structured_profile_data() -> None:
    """EMAIL values under field.email must survive normalization."""
    [message] = _messaging_customer_messages([_form_response_event()])

    assert message["content_type"] == "formResponse"
    assert message["form_data"] == {
        "name": "Ellie",
        "email": "ellie.blackman@atticus.tech",
        "workspace_id": _WORKSPACE_ID,
    }


def test_latest_form_response_wins_when_customer_submits_more_than_once() -> None:
    """Profile consumers must not keep the first, stale workspace submission."""
    messages = _messaging_customer_messages([
        _form_response_event(
            message_id="form-old",
            email="old@example.test",
            workspace_id="old-workspace",
        ),
        _form_response_event(
            message_id="form-new",
            email="new@example.test",
            workspace_id="new-workspace",
        ),
    ])

    assert extract_form_data_from_msgs(messages) == {
        "name": "Ellie",
        "email": "new@example.test",
        "workspace_id": "new-workspace",
    }


@pytest.mark.asyncio
async def test_form_response_is_excluded_from_issue_text_classification() -> None:
    """Profile fields are context, not a second customer problem statement."""
    [form_response] = _messaging_customer_messages([_form_response_event()])
    classify = AsyncMock(return_value=_substantial_verdict())

    await classify_ticket_messages(
        ticket_id=_TICKET_ID,
        customer_msgs=[
            {
                "id": "m1",
                "body": "既存のナレッジ文書を表示できません。",
                "content_type": "text",
                "image_urls": [],
            },
            form_response,
        ],
        model="gpt-test",
        api_key="sk-test",
        classify_messages_fn=classify,
    )

    classify.assert_awaited_once_with(
        messages=["既存のナレッジ文書を表示できません。"],
        model="gpt-test",
        api_key="sk-test",
        base_url=None,
        image_data_urls=[],
        image_count=0,
        enable_vision_input=False,
    )


def test_form_response_is_excluded_from_reply_language_hint() -> None:
    """English profile labels must not override the customer's Japanese."""
    form_response = _form_response_event()
    form_response["content"]["text_fallback"] = (
        "Name: Ellie\nEmail: ellie.blackman@atticus.tech\n"
        f"Workspace ID: {_WORKSPACE_ID}"
    )

    hint = _extract_reply_language_hint(
        thread_type="conversation_log",
        thread_items=[
            {
                **_initial_issue_event(),
                "content": {
                    "type": "text",
                    "text": "既存のナレッジ文書を表示できません。確認をお願いします。",
                },
            },
            _pending_answer_bot_form_event(),
            form_response,
        ],
        ticket_description="",
    )

    assert hint is not None
    assert hint["language"] == "ja"
    assert "ナレッジ" in hint["evidence"]


@pytest.mark.asyncio
async def test_waiting_form_is_polled_for_a_lost_form_response_webhook(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The poller must requeue a later response if its webhook was lost."""
    from zd_agent.trigger import state_store

    monkeypatch.setattr(
        state_store,
        "VALID_STATES",
        state_store.VALID_STATES | {"waiting_for_form"},
    )
    state = TicketStateTracker(tmp_path / "tickets.json")
    state.set(
        _TICKET_ID,
        TicketState(
            state="waiting_for_form",
            first_seen=datetime.now(timezone.utc) - timedelta(minutes=5),
            last_customer_comment_id="m1",
            last_verdict="waiting_for_form",
        ),
    )
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    fetch = AsyncMock(return_value=(
        [
            {"id": "m1", "body": "issue", "content_type": "text"},
            {"id": "m2", "body": "Name: Ellie", "content_type": "formResponse"},
        ],
        {
            "id": _TICKET_ID,
            "status": "new",
            "assignee_id": None,
            "tags": [],
            "via": {"channel": "native_messaging"},
        },
    ))

    with patch("zd_agent.services.ticket_messages._fetch_customer_messages", fetch):
        await scan_once(
            state=state,
            trigger_queue=queue,
            abandoned_hours=24,
            greeting_alert_minutes=30,
        )

    queued = queue.get_nowait()
    assert queued["source"] == "poller_new_msg"
    assert queued["ticket_id"] == _TICKET_ID
    assert queued["_queue_key"] == f"poller_new_msg:{_TICKET_ID}:m2"


@pytest.mark.asyncio
async def test_waiting_form_does_not_inherit_issue_waiting_timeouts(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Form readiness is polled, but it is not a greeting/ask lifecycle."""
    from zd_agent.trigger import state_store

    monkeypatch.setattr(
        state_store,
        "VALID_STATES",
        state_store.VALID_STATES | {"waiting_for_form"},
    )
    state = TicketStateTracker(tmp_path / "tickets.json")
    state.set(
        _TICKET_ID,
        TicketState(
            state="waiting_for_form",
            first_seen=datetime.now(timezone.utc) - timedelta(hours=25),
            last_customer_comment_id="m1",
            last_verdict="waiting_for_form",
        ),
    )
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    fetch = AsyncMock(return_value=(
        [{"id": "m1", "body": "issue", "content_type": "text"}],
        {
            "id": _TICKET_ID,
            "status": "new",
            "assignee_id": None,
            "tags": [],
            "via": {"channel": "native_messaging"},
        },
    ))

    with patch("zd_agent.services.ticket_messages._fetch_customer_messages", fetch):
        processed = await scan_once(
            state=state,
            trigger_queue=queue,
            abandoned_hours=24,
            greeting_alert_minutes=30,
        )

    assert processed == 1
    fetch.assert_awaited_once_with(_TICKET_ID, log_success=False)
    waiting = state.get(_TICKET_ID)
    assert waiting is not None
    assert waiting.state == "waiting_for_form"
    assert waiting.abandoned_alert_sent_at is None
    assert waiting.alert_sent_at is None
    assert queue.empty()
