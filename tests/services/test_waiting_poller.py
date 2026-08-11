"""V2 waiting-poller contracts: recovery, ownership and bounded waits."""

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from zd_agent.config import AppConfig, ZendeskConfig
from zd_agent.services.waiting_poller import AGENT_ASKED_TAG, scan_once
from zd_agent.trigger.state_store import TicketState, TicketStateTracker


def _now():
    return datetime.now(timezone.utc)


@pytest.fixture
def state(tmp_path):
    return TicketStateTracker(tmp_path / "tickets.json")


@pytest.fixture
def queue():
    return asyncio.Queue()


def _ticket(status="new", assignee_id=None, tags=None):
    return {
        "id": 1,
        "status": status,
        "assignee_id": assignee_id,
        "tags": tags or [],
        "via": {"channel": "native_messaging"},
    }


async def _scan(state, queue, *, messages, ticket):
    fetch = AsyncMock(return_value=(messages, ticket))
    with patch("zd_agent.services.ticket_messages._fetch_customer_messages", fetch):
        processed = await scan_once(
            state=state,
            trigger_queue=queue,
            customer_wait_timeout_hours=24,
        )
    return processed, fetch


@pytest.mark.asyncio
async def test_missed_customer_webhook_is_recovered(state, queue):
    state.set(
        1,
        TicketState(
            state="awaiting_customer",
            first_seen=_now() - timedelta(minutes=10),
            last_customer_comment_id="old",
        ),
    )

    processed, fetch = await _scan(
        state,
        queue,
        messages=[{"id": "new", "body": "Here is the trace ID"}],
        ticket=_ticket(),
    )

    assert processed == 1
    fetch.assert_awaited_once_with(1, log_success=False)
    assert queue.get_nowait() == {
        "source": "poller_new_msg",
        "ticket_id": 1,
        "_queue_key": "poller_new_msg:1:new",
    }


@pytest.mark.asyncio
async def test_unchanged_customer_context_within_window_is_noop(state, queue):
    state.set(
        1,
        TicketState(
            state="awaiting_customer",
            first_seen=_now() - timedelta(minutes=5),
            last_agent_finish_ts=_now() - timedelta(minutes=5),
            last_customer_comment_id="same",
        ),
    )

    await _scan(
        state,
        queue,
        messages=[{"id": "same", "body": "waiting"}],
        ticket=_ticket(),
    )

    assert queue.empty()
    assert state.get(1).state == "awaiting_customer"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["open", "pending", "solved"])
async def test_out_of_scope_zendesk_status_stops_automation(state, queue, status):
    state.set(
        1,
        TicketState(
            state="awaiting_customer",
            first_seen=_now(),
            last_customer_comment_id="same",
        ),
    )

    await _scan(
        state,
        queue,
        messages=[{"id": "same", "body": "waiting"}],
        ticket=_ticket(status=status),
    )

    assert state.get(1).state == "out_of_scope"
    assert queue.empty()


@pytest.mark.asyncio
async def test_agent_asked_open_ticket_remains_recoverable(state, queue):
    state.set(
        1,
        TicketState(
            state="awaiting_customer",
            first_seen=_now(),
            last_customer_comment_id="old",
        ),
    )

    await _scan(
        state,
        queue,
        messages=[{"id": "new", "body": "More evidence"}],
        ticket=_ticket(status="open", tags=[AGENT_ASKED_TAG]),
    )

    assert queue.get_nowait()["source"] == "poller_new_msg"
    assert state.get(1).state == "awaiting_customer"


@pytest.mark.asyncio
async def test_human_assignee_stops_automation_without_handoff(state, queue):
    state.set(
        1,
        TicketState(
            state="awaiting_customer",
            first_seen=_now(),
            last_customer_comment_id="same",
        ),
    )

    await _scan(
        state,
        queue,
        messages=[{"id": "same", "body": "waiting"}],
        ticket=_ticket(assignee_id=99999),
    )

    record = state.get(1)
    assert record.state == "externally_owned"
    assert record.ownership_reason == "human_assignee"
    assert record.ownership_effect_reference == ""


@pytest.mark.asyncio
async def test_non_human_assignee_does_not_claim_external_ownership(state, queue):
    state.set(
        1,
        TicketState(
            state="awaiting_customer",
            first_seen=_now(),
            last_agent_finish_ts=_now(),
            last_customer_comment_id="same",
        ),
    )
    cfg = AppConfig()
    cfg.zendesk = ZendeskConfig(
        agent_user_id=45433304089364,
        non_human_assignee_ids=[46493567999892],
    )
    fetch = AsyncMock(
        return_value=(
            [{"id": "same", "body": "waiting"}],
            _ticket(assignee_id=46493567999892),
        )
    )

    with (
        patch("zd_agent.services.ticket_messages._fetch_customer_messages", fetch),
        patch(
            "zd_agent.services.waiting_poller.get_config",
            return_value=cfg,
        ),
    ):
        await scan_once(
            state=state,
            trigger_queue=queue,
            customer_wait_timeout_hours=24,
        )

    assert state.get(1).state == "awaiting_customer"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("waiting_state", "reason"),
    [
        ("awaiting_customer", "customer_response_timeout"),
        ("waiting_for_form", "form_submission_timeout"),
    ],
)
async def test_expired_wait_stops_without_notification_or_handoff(
    state,
    queue,
    waiting_state,
    reason,
):
    state.set(
        1,
        TicketState(
            state=waiting_state,
            first_seen=_now() - timedelta(hours=25),
            last_agent_finish_ts=(
                _now() - timedelta(hours=25)
                if waiting_state == "awaiting_customer"
                else None
            ),
            last_customer_comment_id="same",
        ),
    )

    await _scan(
        state,
        queue,
        messages=[{"id": "same", "body": "waiting"}],
        ticket=_ticket(),
    )

    record = state.get(1)
    assert record.state == "out_of_scope"
    assert record.last_verdict == reason
    assert record.escalated_at is None
    assert record.ownership_effect_reference == ""
    assert queue.empty()


@pytest.mark.asyncio
async def test_in_silence_window_is_scanned_only_for_missed_message(state, queue):
    state.set(
        1,
        TicketState(
            state="in_silence_window",
            first_seen=_now(),
            last_customer_comment_id="old",
            silence_window_end=_now() + timedelta(minutes=2),
        ),
    )

    await _scan(
        state,
        queue,
        messages=[{"id": "new", "body": "follow-up"}],
        ticket=_ticket(),
    )

    assert queue.get_nowait()["source"] == "poller_new_msg"


@pytest.mark.asyncio
async def test_terminal_state_is_never_scanned(state, queue):
    state.set(
        1,
        TicketState(state="processed", first_seen=_now() - timedelta(days=2)),
    )
    fetch = AsyncMock()
    with patch("zd_agent.services.ticket_messages._fetch_customer_messages", fetch):
        processed = await scan_once(
            state=state,
            trigger_queue=queue,
            customer_wait_timeout_hours=24,
        )

    assert processed == 0
    fetch.assert_not_awaited()
