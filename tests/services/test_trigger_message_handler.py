"""Tests for single trigger message handling orchestration."""
from __future__ import annotations

import json
import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from zd_agent.config import AppConfig, PublicReplyConfig, TriggerDeciderConfig, ZendeskConfig
from zd_agent.services.trigger_errors import TriggerDeferredError
from zd_agent.services.trigger_message_handler import handle_trigger_message
from zd_agent.trigger.decider import PreCheckVerdict
from zd_agent.trigger.state_store import TicketState, TicketStateTracker


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
async def test_ticket_fetch_failure_is_retryable(tmp_path) -> None:
    with pytest.raises(TriggerDeferredError, match="ticket_not_materialized"):
        await handle_trigger_message(
            msg={"ticket_id": 12345, "source": "zendesk_webhook"},
            manager=MagicMock(),
            state=TicketStateTracker(tmp_path / "tickets.json"),
            cfg=_cfg(),
            env=SimpleNamespace(openai_api_key="sk-test"),
            log_path=tmp_path / "trigger.jsonl",
            fetch_customer_messages_fn=AsyncMock(return_value=([], None)),
        )


@pytest.mark.asyncio
async def test_customer_message_during_cooldown_is_deferred_without_consuming_it(
    tmp_path,
) -> None:
    now = datetime.now(timezone.utc)
    state = TicketStateTracker(tmp_path / "tickets.json")
    state.set(
        12345,
        TicketState(
            state="processed",
            first_seen=now - timedelta(minutes=10),
            last_agent_finish_ts=now - timedelta(seconds=20),
            last_customer_comment_id="cid-1",
        ),
    )

    with pytest.raises(TriggerDeferredError) as exc_info:
        await handle_trigger_message(
            msg={"ticket_id": 12345, "source": "zendesk_webhook"},
            manager=MagicMock(),
            state=state,
            cfg=_cfg(),
            env=SimpleNamespace(openai_api_key="sk-test"),
            log_path=tmp_path / "trigger.jsonl",
            fetch_customer_messages_fn=AsyncMock(return_value=(
                [{"id": "cid-2", "body": "new issue", "created_at": now.isoformat()}],
                {"id": 12345},
            )),
            escalate_fn=AsyncMock(),
            detect_human_takeover_fn=AsyncMock(return_value=(False, "")),
        )

    assert exc_info.value.reason == "post_agent_cooldown"
    assert 0 < exc_info.value.delay_seconds <= 180
    assert state.get(12345).last_customer_comment_id == "cid-1"


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


@pytest.mark.asyncio
async def test_startup_recovery_reenters_takeover_and_classification_gates(
    tmp_path,
    monkeypatch,
) -> None:
    state = TicketStateTracker(tmp_path / "tickets.json")
    fetch_messages = AsyncMock(return_value=(
        [{"id": "cid-1", "body": "issue detail", "created_at": "2026-06-05T06:00:00Z"}],
        {"id": 12345},
    ))
    detect_takeover = AsyncMock(return_value=(False, ""))
    verdict = PreCheckVerdict(
        verdict="substantial_issue",
        completeness=90,
        reason="has detail",
    )
    classify = AsyncMock(return_value=SimpleNamespace(verdict=verdict))
    post_verdict = AsyncMock()
    monkeypatch.setattr(
        "zd_agent.services.messaging_context.get_messaging_context",
        lambda _ticket_id: {
            "conversation_id": "conversation-restored",
            "app_id": "app-restored",
        },
    )

    result = await handle_trigger_message(
        msg={"ticket_id": 12345, "source": "startup_recovery"},
        manager=MagicMock(),
        state=state,
        cfg=_cfg(),
        env=SimpleNamespace(openai_api_key="sk-test"),
        log_path=tmp_path / "trigger.jsonl",
        fetch_customer_messages_fn=fetch_messages,
        classify_ticket_messages_fn=classify,
        post_verdict_dispatch_fn=post_verdict,
        escalate_fn=AsyncMock(),
        detect_human_takeover_fn=detect_takeover,
    )

    assert result.action == "verdict_dispatched"
    fetch_messages.assert_awaited_once()
    detect_takeover.assert_awaited_once()
    classify.assert_awaited_once()
    assert post_verdict.await_args.kwargs["source"] == "startup_recovery"
    assert post_verdict.await_args.kwargs["conversation_id"] == (
        "conversation-restored"
    )
    assert post_verdict.await_args.kwargs["messaging_app_id"] == "app-restored"


@pytest.mark.asyncio
async def test_startup_recovery_without_messages_does_not_overwrite_newer_state(
    tmp_path,
) -> None:
    """A stale startup worker must not resurrect a ticket after human takeover."""
    state = TicketStateTracker(tmp_path / "tickets.json")
    now = datetime.now(timezone.utc)
    state.set(
        12345,
        TicketState(
            state="processing",
            first_seen=now,
            active_session_id="crashed-session",
        ),
    )
    before_fetch = state.get(12345)
    assert before_fetch is not None

    async def fetch(_ticket_id: int):
        current = state.get(12345)
        assert current is not None
        current.state = "processing"
        current.active_session_id = "new-live-session"
        state.set(12345, current)
        return [], {"id": 12345}

    result = await handle_trigger_message(
        msg={"ticket_id": 12345, "source": "startup_recovery"},
        manager=MagicMock(),
        state=state,
        cfg=_cfg(),
        env=MagicMock(),
        log_path=tmp_path / "trigger.jsonl",
        fetch_customer_messages_fn=fetch,
        escalate_fn=AsyncMock(),
        post_verdict_dispatch_fn=AsyncMock(),
    )

    assert result.action == "startup_recovery_waiting"
    assert state.get(12345).state == "processing"
    assert state.get(12345).active_session_id == "new-live-session"


@pytest.mark.asyncio
async def test_concurrent_webhooks_allow_only_one_verdict_operation(tmp_path) -> None:
    state = TicketStateTracker(tmp_path / "tickets.json")
    entered = asyncio.Event()
    release = asyncio.Event()
    classifier_calls = 0

    async def classify(**_kwargs):
        nonlocal classifier_calls
        classifier_calls += 1
        entered.set()
        await release.wait()
        return SimpleNamespace(verdict=PreCheckVerdict(
            verdict="substantial_issue", completeness=90, reason="detail",
        ))

    async def fetch(_ticket_id: int):
        return ([{"id": "m1", "body": "it fails", "created_at": "2026-06-05T06:00:00Z"}], {"id": 12345})

    kwargs = dict(
        msg={"ticket_id": 12345, "source": "zendesk_webhook", "_queue_key": "event-a"},
        manager=MagicMock(), state=state, cfg=_cfg(),
        env=SimpleNamespace(openai_api_key="sk-test"), log_path=tmp_path / "trigger.jsonl",
        fetch_customer_messages_fn=fetch, classify_ticket_messages_fn=classify,
        post_verdict_dispatch_fn=AsyncMock(), escalate_fn=AsyncMock(),
        detect_human_takeover_fn=AsyncMock(return_value=(False, "")),
    )
    first = asyncio.create_task(handle_trigger_message(**kwargs))
    await entered.wait()
    with pytest.raises(TriggerDeferredError) as deferred:
        await handle_trigger_message(**{**kwargs, "msg": {**kwargs["msg"], "_queue_key": "event-b"}})
    assert deferred.value.reason == "active_session_processing"
    release.set()
    assert (await first).action == "verdict_dispatched"
    assert classifier_calls == 1


@pytest.mark.asyncio
async def test_classifier_failure_releases_verdict_operation_claim(tmp_path) -> None:
    state = TicketStateTracker(tmp_path / "tickets.json")
    async def fetch(_ticket_id: int):
        return ([{"id": "m1", "body": "it fails", "created_at": "2026-06-05T06:00:00Z"}], {"id": 12345})

    with pytest.raises(RuntimeError, match="classifier down"):
        await handle_trigger_message(
            msg={"ticket_id": 12345, "source": "zendesk_webhook", "_queue_key": "event-a"},
            manager=MagicMock(), state=state, cfg=_cfg(),
            env=SimpleNamespace(openai_api_key="sk-test"), log_path=tmp_path / "trigger.jsonl",
            fetch_customer_messages_fn=fetch,
            classify_ticket_messages_fn=AsyncMock(side_effect=RuntimeError("classifier down")),
            post_verdict_dispatch_fn=AsyncMock(), escalate_fn=AsyncMock(),
            detect_human_takeover_fn=AsyncMock(return_value=(False, "")),
        )
    assert state.get(12345) is None
