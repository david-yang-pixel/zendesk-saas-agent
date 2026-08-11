"""Tests for single trigger message handling orchestration."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from zd_agent.config import AppConfig, IntakeConfig, PublicReplyConfig, ZendeskConfig
from zd_agent.casework.domain.enums import ExecutionMode
from zd_agent.services.trigger_errors import TriggerDeferredError
from zd_agent.services.trigger_message_handler import handle_trigger_message
from zd_agent.trigger.state_store import TicketState, TicketStateTracker


def _cfg() -> AppConfig:
    cfg = AppConfig()
    cfg.casework.mode = ExecutionMode.CASEWORK
    cfg.public_reply = PublicReplyConfig(enabled=False)
    cfg.zendesk = ZendeskConfig(
        subdomain="x",
        email="x@y.com",
        api_token="t",
        min_ticket_id=0,
        agent_user_id=45433304089364,
    )
    cfg.intake = IntakeConfig(
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
            log_path=tmp_path / "trigger.jsonl",
            fetch_customer_messages_fn=AsyncMock(
                return_value=(
                    [
                        {
                            "id": "cid-2",
                            "body": "new issue",
                            "created_at": now.isoformat(),
                        }
                    ],
                    {"id": 12345},
                )
            ),
            human_takeover_fn=AsyncMock(),
            detect_human_takeover_fn=AsyncMock(return_value=(False, "")),
        )

    assert exc_info.value.reason == "post_agent_cooldown"
    assert 0 < exc_info.value.delay_seconds <= 180
    assert state.get(12345).last_customer_comment_id == "cid-1"


@pytest.mark.asyncio
async def test_customer_message_enters_v2_without_legacy_classification(
    tmp_path,
) -> None:
    state = TicketStateTracker(tmp_path / "tickets.json")
    manager = MagicMock()

    result = await handle_trigger_message(
        msg={"ticket_id": 12345, "source": "zendesk_webhook"},
        manager=manager,
        state=state,
        cfg=_cfg(),
        log_path=tmp_path / "trigger.jsonl",
        fetch_customer_messages_fn=AsyncMock(
            return_value=(
                [
                    {
                        "id": "cid-1",
                        "body": "it fails",
                        "created_at": "2026-06-05T06:00:00Z",
                    }
                ],
                {"id": 12345},
            )
        ),
        human_takeover_fn=AsyncMock(),
        detect_human_takeover_fn=AsyncMock(return_value=(False, "")),
    )

    assert result.handled is True
    assert result.action == "casework_queued"
    queued = state.get(12345)
    assert queued is not None
    assert queued.last_verdict == "casework_ready"


@pytest.mark.asyncio
async def test_casework_mode_queues_v2_without_legacy_verdict_or_public_template(
    tmp_path,
) -> None:
    state = TicketStateTracker(tmp_path / "tickets.json")
    cfg = _cfg()
    cfg.casework.mode = ExecutionMode.CASEWORK

    result = await handle_trigger_message(
        msg={"ticket_id": 12345, "source": "zendesk_webhook"},
        manager=MagicMock(),
        state=state,
        cfg=cfg,
        log_path=tmp_path / "trigger.jsonl",
        fetch_customer_messages_fn=AsyncMock(
            return_value=(
                [
                    {
                        "id": "cid-1",
                        "body": "hello",
                        "created_at": "2026-06-05T06:00:00Z",
                    }
                ],
                {"id": 12345},
            )
        ),
        human_takeover_fn=AsyncMock(),
        detect_human_takeover_fn=AsyncMock(return_value=(False, "")),
    )

    assert result.action == "casework_queued"
    queued = state.get(12345)
    assert queued is not None
    assert queued.state == "in_silence_window"
    assert queued.last_verdict == "casework_ready"


@pytest.mark.asyncio
async def test_canary_selected_ticket_queues_v2_without_legacy_fallback(
    tmp_path,
) -> None:
    state = TicketStateTracker(tmp_path / "tickets.json")
    cfg = _cfg()
    cfg.casework.mode = ExecutionMode.CANARY
    cfg.casework.canary_ticket_ids = (12345,)

    result = await handle_trigger_message(
        msg={"ticket_id": 12345, "source": "zendesk_webhook"},
        manager=MagicMock(),
        state=state,
        cfg=cfg,
        log_path=tmp_path / "trigger.jsonl",
        fetch_customer_messages_fn=AsyncMock(
            return_value=(
                [
                    {
                        "id": "cid-1",
                        "body": "hello",
                        "created_at": "2026-06-05T06:00:00Z",
                    }
                ],
                {"id": 12345},
            )
        ),
        human_takeover_fn=AsyncMock(),
        detect_human_takeover_fn=AsyncMock(return_value=(False, "")),
    )

    assert result.action == "casework_queued"
    assert state.get(12345).last_verdict == "casework_ready"


@pytest.mark.asyncio
async def test_canary_non_selected_ticket_is_deferred_before_external_actions(
    tmp_path,
) -> None:
    cfg = _cfg()
    cfg.casework.mode = ExecutionMode.CANARY
    cfg.casework.canary_ticket_ids = (99999,)
    manager = MagicMock()
    manager.dispatch = AsyncMock()
    fetch = AsyncMock()
    human_takeover = AsyncMock()

    with pytest.raises(TriggerDeferredError) as exc_info:
        await handle_trigger_message(
            msg={"ticket_id": 12345, "source": "zendesk_webhook"},
            manager=manager,
            state=TicketStateTracker(tmp_path / "tickets.json"),
            cfg=cfg,
            log_path=tmp_path / "trigger.jsonl",
            fetch_customer_messages_fn=fetch,
            human_takeover_fn=human_takeover,
        )

    assert exc_info.value.reason == "casework_canary_not_selected"
    assert exc_info.value.delay_seconds == 60.0
    fetch.assert_not_awaited()
    manager.dispatch.assert_not_awaited()
    human_takeover.assert_not_awaited()


@pytest.mark.parametrize("source", ["mention", "silence_waker"])
@pytest.mark.asyncio
async def test_canary_non_selected_direct_source_never_dispatches(
    tmp_path,
    source: str,
) -> None:
    cfg = _cfg()
    cfg.casework.mode = ExecutionMode.CANARY
    cfg.casework.canary_ticket_ids = ()
    state = TicketStateTracker(tmp_path / f"tickets-{source}.json")
    msg = {"ticket_id": 12345, "source": source}
    if source == "silence_waker":
        now = datetime.now(timezone.utc)
        state.set(
            12345,
            TicketState(
                state="in_silence_window",
                first_seen=now,
                silence_window_end=now + timedelta(minutes=1),
            ),
        )
        current = state.get(12345)
        msg.update(
            expected_state_revision=current.state_revision,
            expected_silence_window_end=current.silence_window_end.isoformat(),
        )
    manager = MagicMock()
    manager.dispatch = AsyncMock()
    fetch = AsyncMock()

    with pytest.raises(TriggerDeferredError) as exc_info:
        await handle_trigger_message(
            msg=msg,
            manager=manager,
            state=state,
            cfg=cfg,
            log_path=tmp_path / "trigger.jsonl",
            fetch_customer_messages_fn=fetch,
            human_takeover_fn=AsyncMock(),
        )

    assert exc_info.value.reason == "casework_canary_not_selected"
    manager.dispatch.assert_not_awaited()
    fetch.assert_not_awaited()


@pytest.mark.asyncio
async def test_startup_recovery_reenters_takeover_and_v2_intake_gates(
    tmp_path,
) -> None:
    state = TicketStateTracker(tmp_path / "tickets.json")
    fetch_messages = AsyncMock(
        return_value=(
            [
                {
                    "id": "cid-1",
                    "body": "issue detail",
                    "created_at": "2026-06-05T06:00:00Z",
                }
            ],
            {"id": 12345},
        )
    )
    detect_takeover = AsyncMock(return_value=(False, ""))

    result = await handle_trigger_message(
        msg={"ticket_id": 12345, "source": "startup_recovery"},
        manager=MagicMock(),
        state=state,
        cfg=_cfg(),
        log_path=tmp_path / "trigger.jsonl",
        fetch_customer_messages_fn=fetch_messages,
        human_takeover_fn=AsyncMock(),
        detect_human_takeover_fn=detect_takeover,
    )

    assert result.action == "casework_queued"
    fetch_messages.assert_awaited_once()
    detect_takeover.assert_awaited_once()
    assert state.get(12345).last_verdict == "casework_ready"


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
        log_path=tmp_path / "trigger.jsonl",
        fetch_customer_messages_fn=fetch,
        human_takeover_fn=AsyncMock(),
    )

    assert result.action == "startup_recovery_waiting"
    assert state.get(12345).state == "processing"
    assert state.get(12345).active_session_id == "new-live-session"


@pytest.mark.asyncio
async def test_replayed_webhook_does_not_create_a_second_casework_generation(
    tmp_path,
) -> None:
    state = TicketStateTracker(tmp_path / "tickets.json")

    async def fetch(_ticket_id: int):
        return (
            [{"id": "m1", "body": "it fails", "created_at": "2026-06-05T06:00:00Z"}],
            {"id": 12345},
        )

    kwargs = dict(
        msg={"ticket_id": 12345, "source": "zendesk_webhook", "_queue_key": "event-a"},
        manager=MagicMock(),
        state=state,
        cfg=_cfg(),
        log_path=tmp_path / "trigger.jsonl",
        fetch_customer_messages_fn=fetch,
        human_takeover_fn=AsyncMock(),
        detect_human_takeover_fn=AsyncMock(return_value=(False, "")),
    )
    first = await handle_trigger_message(**kwargs)
    first_generation = state.get(12345).state_revision
    replay = await handle_trigger_message(
        **{**kwargs, "msg": {**kwargs["msg"], "_queue_key": "event-b"}},
    )

    assert first.action == "casework_queued"
    assert replay.action == "duplicate_customer_comment"
    assert state.get(12345).state_revision == first_generation


@pytest.mark.asyncio
async def test_v2_intake_does_not_require_legacy_classifier(tmp_path) -> None:
    state = TicketStateTracker(tmp_path / "tickets.json")

    async def fetch(_ticket_id: int):
        return (
            [{"id": "m1", "body": "it fails", "created_at": "2026-06-05T06:00:00Z"}],
            {"id": 12345},
        )

    result = await handle_trigger_message(
        msg={"ticket_id": 12345, "source": "zendesk_webhook", "_queue_key": "event-a"},
        manager=MagicMock(),
        state=state,
        cfg=_cfg(),
        log_path=tmp_path / "trigger.jsonl",
        fetch_customer_messages_fn=fetch,
        human_takeover_fn=AsyncMock(),
        detect_human_takeover_fn=AsyncMock(return_value=(False, "")),
    )

    assert result.action == "casework_queued"
    assert state.get(12345).last_verdict == "casework_ready"
