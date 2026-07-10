"""Tests for trigger side-effect adapters."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from zd_agent.services.trigger_actions import (
    handle_silence_expired_ask_action,
    post_verdict_dispatch_action,
    send_ask_template_action,
    set_escalated_to_human_action,
)


@pytest.mark.asyncio
async def test_send_ask_template_action_delegates(monkeypatch) -> None:
    send_ask = AsyncMock(return_value=True)
    monkeypatch.setattr(
        "zd_agent.services.trigger_actions.send_ask_template_best_effort",
        send_ask,
    )

    result = await send_ask_template_action(
        ticket_id=2808,
        verdict_kind="greeting",
        customer_msgs=[{"body": "hello"}],
        ticket_info={"id": 2808},
        ask_count=1,
        source="zendesk_webhook",
        conversation_id="conv-1",
        messaging_app_id="app-1",
        delivery_attempt_id="",
    )

    assert result is True
    send_ask.assert_awaited_once_with(
        ticket_id=2808,
        verdict_kind="greeting",
        customer_msgs=[{"body": "hello"}],
        ask_count=1,
        source="zendesk_webhook",
        conversation_id="conv-1",
        messaging_app_id="app-1",
        delivery_attempt_id="",
    )


@pytest.mark.asyncio
async def test_silence_expired_ask_action_delegates(monkeypatch, tmp_path) -> None:
    handle_silence = AsyncMock(return_value=None)
    monkeypatch.setattr(
        "zd_agent.services.trigger_actions.handle_silence_expired_ask",
        handle_silence,
    )

    await handle_silence_expired_ask_action(
        ticket_id=2808,
        state="state",
        log_path=tmp_path / "trigger.jsonl",
        nudge_message_fn="nudge",
        delivery_attempt_id="",
    )

    handle_silence.assert_awaited_once_with(
        ticket_id=2808,
        state="state",
        log_path=tmp_path / "trigger.jsonl",
        nudge_message_fn="nudge",
        delivery_attempt_id="",
    )


@pytest.mark.asyncio
async def test_set_escalated_to_human_action_delegates(monkeypatch, tmp_path) -> None:
    set_escalated = AsyncMock(return_value=None)
    monkeypatch.setattr(
        "zd_agent.services.trigger_actions.set_escalated_to_human",
        set_escalated,
    )
    now = datetime(2026, 6, 5, 14, 30, tzinfo=timezone.utc)

    await set_escalated_to_human_action(
        ticket_id=2808,
        reason="customer_requested",
        existing="existing",
        state="state",
        log_path=tmp_path / "trigger.jsonl",
        source="zendesk_webhook",
        now=now,
        verdict_str="request_human",
        latest_cid="cid-1",
        latest_ts=now,
        completeness=100,
        customer_msgs=[{"body": "human"}],
        customer_phrase="human",
        agent_last_reply="reply",
        extra_log={"k": "v"},
    )

    set_escalated.assert_awaited_once_with(
        ticket_id=2808,
        reason="customer_requested",
        existing="existing",
        state="state",
        log_path=tmp_path / "trigger.jsonl",
        source="zendesk_webhook",
        now=now,
        verdict_str="request_human",
        latest_cid="cid-1",
        latest_ts=now,
        completeness=100,
        customer_msgs=[{"body": "human"}],
        customer_phrase="human",
        agent_last_reply="reply",
        extra_log={"k": "v"},
    )


@pytest.mark.asyncio
async def test_post_verdict_dispatch_action_delegates(monkeypatch, tmp_path) -> None:
    handle_dispatch = AsyncMock(return_value=None)
    monkeypatch.setattr(
        "zd_agent.services.trigger_actions.handle_post_verdict_dispatch",
        handle_dispatch,
    )
    verdict = object()
    now = datetime(2026, 6, 5, 14, 35, tzinfo=timezone.utc)

    await post_verdict_dispatch_action(
        ticket_id=2808,
        verdict=verdict,
        customer_msgs=[{"body": "hello"}],
        ticket_info={"id": 2808},
        latest_cid="cid-1",
        latest_ts=now,
        existing="existing",
        state="state",
        log_path=tmp_path / "trigger.jsonl",
        source="zendesk_webhook",
        now=now,
        cfg="cfg",
    )

    handle_dispatch.assert_awaited_once()
    kwargs = handle_dispatch.await_args.kwargs
    assert kwargs["ticket_id"] == 2808
    assert kwargs["verdict"] is verdict
    assert kwargs["customer_msgs"] == [{"body": "hello"}]
    assert kwargs["ticket_info"] == {"id": 2808}
    assert kwargs["latest_cid"] == "cid-1"
    assert kwargs["latest_ts"] == now
    assert kwargs["existing"] == "existing"
    assert kwargs["state"] == "state"
    assert kwargs["log_path"] == tmp_path / "trigger.jsonl"
    assert kwargs["source"] == "zendesk_webhook"
    assert kwargs["now"] == now
    assert kwargs["cfg"] == "cfg"
    assert kwargs["escalate_fn"] is set_escalated_to_human_action
    assert kwargs["send_ask_fn"] is send_ask_template_action
