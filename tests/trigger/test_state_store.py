"""Test TicketStateTracker persistence + state semantics."""
from datetime import datetime, timezone
from pathlib import Path

import pytest

from zd_agent.trigger.state_store import (
    TicketState,
    TicketStateTracker,
    VALID_STATES,
)


def _now():
    return datetime.now(timezone.utc)


def test_set_and_get(tmp_path):
    store = TicketStateTracker(tmp_path / "tickets.json")
    store.set("12345", TicketState(
        state="waiting_for_issue",
        first_seen=_now(),
        last_customer_comment_id=67890,
    ))
    rec = store.get("12345")
    assert rec is not None
    assert rec.state == "waiting_for_issue"
    assert rec.last_customer_comment_id == 67890


def test_get_missing_returns_none(tmp_path):
    store = TicketStateTracker(tmp_path / "tickets.json")
    assert store.get("nonexistent") is None


def test_accepts_int_or_str_ticket_id(tmp_path):
    store = TicketStateTracker(tmp_path / "tickets.json")
    store.set(123, TicketState(state="processed", first_seen=_now()))
    assert store.get(123).state == "processed"
    assert store.get("123").state == "processed"


def test_persistence_across_instances(tmp_path):
    path = tmp_path / "tickets.json"
    store1 = TicketStateTracker(path)
    store1.set("1", TicketState(state="processed", first_seen=_now()))
    store2 = TicketStateTracker(path)
    assert store2.get("1").state == "processed"


def test_list_by_state(tmp_path):
    store = TicketStateTracker(tmp_path / "tickets.json")
    t = _now()
    store.set("1", TicketState(state="in_silence_window", first_seen=t))
    store.set("2", TicketState(state="in_silence_window", first_seen=t))
    store.set("3", TicketState(state="processed", first_seen=t))
    assert set(store.list_by_state("in_silence_window")) == {"1", "2"}
    assert set(store.list_by_state("processed")) == {"3"}


def test_delete(tmp_path):
    store = TicketStateTracker(tmp_path / "tickets.json")
    store.set("1", TicketState(state="processed", first_seen=_now()))
    store.delete("1")
    assert store.get("1") is None


def test_rejects_invalid_state(tmp_path):
    store = TicketStateTracker(tmp_path / "tickets.json")
    with pytest.raises(ValueError, match="invalid state"):
        store.set("1", TicketState(state="bogus_state", first_seen=_now()))


def test_datetime_roundtrip(tmp_path):
    path = tmp_path / "tickets.json"
    store = TicketStateTracker(path)
    now = _now()
    store.set("1", TicketState(
        state="in_silence_window",
        first_seen=now,
        last_comment_ts=now,
        silence_window_end=now,
    ))
    store2 = TicketStateTracker(path)
    rec = store2.get("1")
    assert rec.first_seen.isoformat() == now.isoformat()
    assert rec.last_comment_ts.isoformat() == now.isoformat()


def test_corrupted_json_yields_empty_store(tmp_path):
    path = tmp_path / "tickets.json"
    path.write_text("not json at all", encoding="utf-8")
    store = TicketStateTracker(path)
    assert len(store) == 0


def test_atomic_save_writes_via_tmp_file(tmp_path):
    path = tmp_path / "tickets.json"
    store = TicketStateTracker(path)
    store.set("1", TicketState(state="processed", first_seen=_now()))
    # After save, .tmp should not exist
    assert not path.with_suffix(path.suffix + ".tmp").exists()
    assert path.exists()


def test_valid_states_constant_has_expected_entries():
    assert "waiting_for_issue" in VALID_STATES
    assert "in_silence_window" in VALID_STATES
    assert "processing" in VALID_STATES
    assert "processed" in VALID_STATES
    assert "waiting_for_auth" in VALID_STATES
    assert "abandoned" in VALID_STATES


def test_last_agent_finish_ts_persists(tmp_path):
    """post-agent cooldown 字段往返序列化。"""
    path = tmp_path / "tickets.json"
    finish_ts = _now()
    store1 = TicketStateTracker(path)
    store1.set("1", TicketState(
        state="processed",
        first_seen=_now(),
        last_agent_finish_ts=finish_ts,
    ))
    store2 = TicketStateTracker(path)
    rec = store2.get("1")
    assert rec is not None
    assert rec.last_agent_finish_ts is not None
    assert rec.last_agent_finish_ts.isoformat() == finish_ts.isoformat()


def test_last_agent_finish_ts_default_none(tmp_path):
    """老 state.json 没这个字段时 from_dict 默认 None。"""
    store = TicketStateTracker(tmp_path / "tickets.json")
    store.set("1", TicketState(state="waiting_for_issue", first_seen=_now()))
    rec = store.get("1")
    assert rec.last_agent_finish_ts is None
