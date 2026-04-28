"""Test Silence Waker: scan once + infinite loop (limited via cancel)."""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from zd_agent.entry.silence_waker import run_silence_waker, scan_once
from zd_agent.trigger.state_store import TicketState, TicketStateTracker


def _now():
    return datetime.now(timezone.utc)


@pytest.mark.asyncio
async def test_scan_once_dispatches_substantial_on_expiry(tmp_path):
    """verdict=substantial → state=processing + source=silence_waker."""
    state = TicketStateTracker(tmp_path / "tickets.json")
    queue: asyncio.Queue = asyncio.Queue()
    state.set("1", TicketState(
        state="in_silence_window",
        first_seen=_now() - timedelta(minutes=10),
        silence_window_end=_now() - timedelta(seconds=1),
        last_verdict="substantial_issue",
    ))
    n = await scan_once(state=state, trigger_queue=queue)
    assert n == 1
    msg = queue.get_nowait()
    assert msg["source"] == "silence_waker"
    assert msg["ticket_id"] == 1
    rec = state.get("1")
    assert rec.state == "processing"


@pytest.mark.asyncio
async def test_scan_once_asks_when_not_substantial_on_expiry(tmp_path):
    """verdict=greeting/insufficient → state=waiting_for_issue + source=silence_expired_ask."""
    for verdict in ("greeting", "insufficient_info", ""):
        state = TicketStateTracker(tmp_path / f"tickets_{verdict or 'blank'}.json")
        queue: asyncio.Queue = asyncio.Queue()
        state.set("1", TicketState(
            state="in_silence_window",
            first_seen=_now() - timedelta(minutes=10),
            silence_window_end=_now() - timedelta(seconds=1),
            last_verdict=verdict,
        ))
        n = await scan_once(state=state, trigger_queue=queue)
        assert n == 1
        msg = queue.get_nowait()
        assert msg["source"] == "silence_expired_ask", verdict
        rec = state.get("1")
        assert rec.state == "waiting_for_issue", verdict


@pytest.mark.asyncio
async def test_scan_once_skips_unexpired(tmp_path):
    state = TicketStateTracker(tmp_path / "tickets.json")
    queue: asyncio.Queue = asyncio.Queue()
    state.set("1", TicketState(
        state="in_silence_window",
        first_seen=_now(),
        silence_window_end=_now() + timedelta(minutes=5),
    ))
    n = await scan_once(state=state, trigger_queue=queue)
    assert n == 0
    assert queue.empty()


@pytest.mark.asyncio
async def test_scan_once_ignores_other_states(tmp_path):
    state = TicketStateTracker(tmp_path / "tickets.json")
    queue: asyncio.Queue = asyncio.Queue()
    # Processed ticket, even if silence_end in the past, should not wake
    state.set("1", TicketState(
        state="processed",
        first_seen=_now() - timedelta(hours=1),
        silence_window_end=_now() - timedelta(hours=1),
    ))
    n = await scan_once(state=state, trigger_queue=queue)
    assert n == 0


@pytest.mark.asyncio
async def test_scan_once_skips_missing_silence_end(tmp_path):
    state = TicketStateTracker(tmp_path / "tickets.json")
    queue: asyncio.Queue = asyncio.Queue()
    state.set("1", TicketState(
        state="in_silence_window",
        first_seen=_now(),
        silence_window_end=None,
    ))
    n = await scan_once(state=state, trigger_queue=queue)
    assert n == 0


@pytest.mark.asyncio
async def test_scan_once_wakes_many(tmp_path):
    state = TicketStateTracker(tmp_path / "tickets.json")
    queue: asyncio.Queue = asyncio.Queue()
    past = _now() - timedelta(seconds=1)
    state.set("1", TicketState(state="in_silence_window", first_seen=past, silence_window_end=past))
    state.set("2", TicketState(state="in_silence_window", first_seen=past, silence_window_end=past))
    state.set("3", TicketState(state="in_silence_window", first_seen=past, silence_window_end=past))
    n = await scan_once(state=state, trigger_queue=queue)
    assert n == 3
    assert queue.qsize() == 3


@pytest.mark.asyncio
async def test_run_silence_waker_loop_can_be_cancelled(tmp_path):
    state = TicketStateTracker(tmp_path / "tickets.json")
    queue: asyncio.Queue = asyncio.Queue()
    task = asyncio.create_task(
        run_silence_waker(
            state=state,
            trigger_queue=queue,
            scan_interval_seconds=1,
        )
    )
    await asyncio.sleep(0.05)  # let it start
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_run_silence_waker_loop_handles_exception(tmp_path, monkeypatch):
    """Exceptions in scan_once should be swallowed per-iteration."""
    state = TicketStateTracker(tmp_path / "tickets.json")
    queue: asyncio.Queue = asyncio.Queue()

    call_count = {"n": 0}

    async def fake_scan_once(**kwargs):
        call_count["n"] += 1
        raise RuntimeError("boom")

    monkeypatch.setattr("zd_agent.entry.silence_waker.scan_once", fake_scan_once)

    task = asyncio.create_task(
        run_silence_waker(
            state=state, trigger_queue=queue, scan_interval_seconds=0.01,
        )
    )
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    # Should have been called multiple times despite the exception each time
    assert call_count["n"] >= 2
