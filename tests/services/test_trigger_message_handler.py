"""Tests for single trigger message handling orchestration."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from zd_agent.config import AppConfig, PublicReplyConfig, TriggerDeciderConfig, ZendeskConfig
from zd_agent.services.trigger_message_handler import handle_trigger_message
from zd_agent.trigger.decider import PreCheckVerdict
from zd_agent.trigger.state_store import TicketStateTracker


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
        post_agent_cooldown_seconds=180,
    )
    return cfg


@pytest.mark.asyncio
async def test_direct_dispatch_message_is_handled_without_fetching(tmp_path) -> None:
    state = TicketStateTracker(tmp_path / "tickets.json")
    manager = MagicMock()
    manager.dispatch = AsyncMock()
    fetch_messages = AsyncMock()

    result = await handle_trigger_message(
        msg={"ticket_id": 12345, "source": "mention", "hint": "check"},
        manager=manager,
        state=state,
        cfg=_cfg(),
        env=SimpleNamespace(openai_api_key="sk-test"),
        log_path=tmp_path / "trigger.jsonl",
        fetch_customer_messages_fn=fetch_messages,
    )

    assert result.handled is True
    assert result.action == "direct_dispatch"
    manager.dispatch.assert_awaited_once()
    fetch_messages.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_customer_messages_logs_skip(tmp_path) -> None:
    state = TicketStateTracker(tmp_path / "tickets.json")
    manager = MagicMock()
    manager.dispatch = AsyncMock()
    log_path = tmp_path / "trigger.jsonl"

    result = await handle_trigger_message(
        msg={"ticket_id": 12345, "source": "zendesk_webhook"},
        manager=manager,
        state=state,
        cfg=_cfg(),
        env=SimpleNamespace(openai_api_key="sk-test"),
        log_path=log_path,
        fetch_customer_messages_fn=AsyncMock(return_value=([], {"id": 12345})),
    )

    assert result.handled is True
    assert result.action == "no_customer_message"
    manager.dispatch.assert_not_awaited()
    [record] = log_path.read_text(encoding="utf-8").splitlines()
    payload = json.loads(record)
    assert payload["ticket"] == "12345"
    assert payload["verdict"] == "no_customer_message"
    assert payload["action"] == "skip"


@pytest.mark.asyncio
async def test_customer_message_classifies_and_dispatches_verdict(tmp_path) -> None:
    state = TicketStateTracker(tmp_path / "tickets.json")
    manager = MagicMock()
    verdict = PreCheckVerdict(
        verdict="substantial_issue",
        completeness=90,
        reason="has detail",
    )
    post_verdict = AsyncMock()

    result = await handle_trigger_message(
        msg={"ticket_id": 12345, "source": "zendesk_webhook"},
        manager=manager,
        state=state,
        cfg=_cfg(),
        env=SimpleNamespace(openai_api_key="sk-test"),
        log_path=Path(tmp_path) / "trigger.jsonl",
        fetch_customer_messages_fn=AsyncMock(return_value=(
            [{"id": "cid-1", "body": "it fails", "created_at": "2026-06-05T06:00:00Z"}],
            {"id": 12345},
        )),
        classify_ticket_messages_fn=AsyncMock(
            return_value=SimpleNamespace(verdict=verdict),
        ),
        post_verdict_dispatch_fn=post_verdict,
        escalate_fn=AsyncMock(),
        detect_human_takeover_fn=AsyncMock(return_value=(False, "")),
    )

    assert result.handled is True
    assert result.action == "verdict_dispatched"
    post_verdict.assert_awaited_once()
    kwargs = post_verdict.await_args.kwargs
    assert kwargs["ticket_id"] == 12345
    assert kwargs["verdict"] is verdict
    assert kwargs["latest_cid"] == "cid-1"
    assert kwargs["source"] == "zendesk_webhook"
