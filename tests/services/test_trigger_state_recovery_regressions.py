"""Regression tests for trigger-state recovery failures."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from zd_agent.config import AppConfig, PublicReplyConfig, TriggerDeciderConfig, ZendeskConfig
from zd_agent.services.direct_dispatch import handle_direct_dispatch_source
from zd_agent.services.trigger_message_handler import handle_trigger_message
from zd_agent.trigger.state_store import TicketState, TicketStateTracker


def _config() -> AppConfig:
    config = AppConfig()
    config.public_reply = PublicReplyConfig(enabled=False)
    config.zendesk = ZendeskConfig(
        subdomain="x",
        email="x@y.com",
        api_token="t",
        min_ticket_id=0,
        agent_user_id=45433304089364,
    )
    config.trigger_decider = TriggerDeciderConfig(
        llm_model="gpt-test",
        post_agent_cooldown_seconds=180,
    )
    return config


@pytest.mark.asyncio
async def test_startup_recovery_reprocesses_matching_saved_comment(
    tmp_path,
) -> None:
    """A recovered processing ticket must not be mistaken for a duplicate."""
    message_time = datetime(2026, 7, 16, 4, 18, 53, tzinfo=timezone.utc)
    state = TicketStateTracker(tmp_path / "tickets.json")
    state.set(
        3461,
        TicketState(
            state="processing",
            first_seen=message_time - timedelta(minutes=3),
            last_customer_comment_id="form-response-1",
            last_comment_ts=message_time,
            last_eval_at=message_time,
            last_verdict="substantial_issue",
        ),
    )
    result = await handle_trigger_message(
        msg={"ticket_id": 3461, "source": "startup_recovery"},
        manager=MagicMock(),
        state=state,
        cfg=_config(),
        log_path=tmp_path / "trigger.jsonl",
        fetch_customer_messages_fn=AsyncMock(return_value=(
            [{
                "id": "form-response-1",
                "body": "Name: Ellie\nWorkspace ID: 86628345-731d-4475-b681-5006f48a2287",
                "created_at": message_time.isoformat(),
            }],
            {"id": 3461},
        )),
        human_takeover_fn=AsyncMock(),
        detect_human_takeover_fn=AsyncMock(return_value=(False, "")),
    )

    assert result.action == "casework_queued"
    recovered = state.get(3461)
    assert recovered is not None
    assert recovered.state == "in_silence_window"
    assert recovered.last_verdict == "casework_ready"


@pytest.mark.asyncio
async def test_direct_dispatch_failure_restores_previous_non_processing_state(
    tmp_path,
) -> None:
    """A failed dispatch must not strand the ticket in processing."""
    now = datetime(2026, 7, 16, 4, 30, tzinfo=timezone.utc)
    state = TicketStateTracker(tmp_path / "tickets.json")
    state.set(
        3461,
        TicketState(
            state="waiting_for_issue",
            first_seen=now - timedelta(minutes=15),
            last_customer_comment_id="form-response-1",
            last_comment_ts=now - timedelta(minutes=12),
            last_verdict="insufficient_info",
        ),
    )
    manager = MagicMock()
    manager.dispatch = AsyncMock(side_effect=RuntimeError("dispatch unavailable"))

    with pytest.raises(RuntimeError, match="dispatch unavailable"):
        await handle_direct_dispatch_source(
            ticket_id=3461,
            msg={"source": "mention", "hint": "retry investigation"},
            manager=manager,
            state=state,
            log_path=tmp_path / "trigger.jsonl",
            cfg=_config(),
            now=now,
        )

    restored = state.get(3461)
    assert restored is not None
    assert restored.state == "waiting_for_issue"
    assert restored.last_customer_comment_id == "form-response-1"
