"""Test TicketStateTracker persistence + state semantics."""

from dataclasses import replace
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from zd_agent.trigger.state_store import (
    TicketStateCorruptedError,
    TicketState,
    TicketStateTracker,
    VALID_STATES,
)


def _now():
    return datetime.now(timezone.utc)


def test_set_and_get(tmp_path):
    store = TicketStateTracker(tmp_path / "tickets.json")
    store.set(
        "12345",
        TicketState(
            state="waiting_for_issue",
            first_seen=_now(),
            last_customer_comment_id=67890,
        ),
    )
    rec = store.get("12345")
    assert rec is not None
    assert rec.state == "waiting_for_issue"
    assert rec.last_customer_comment_id == 67890


def test_update_if_revision_commits_one_fresh_generation(tmp_path) -> None:
    store = TicketStateTracker(tmp_path / "tickets.json")
    store.set(
        "1",
        TicketState(state="in_silence_window", first_seen=_now()),
    )
    before = store.get("1")
    assert before is not None

    commit = store.update_if_revision(
        "1",
        expected_revision=before.state_revision,
        expected_state="in_silence_window",
        mutate=lambda current: replace(
            current,
            state="processing",
            active_session_id="session-1",
        ),
    )

    assert commit is not None
    assert commit.after.state_revision == before.state_revision + 1
    assert store.get("1").active_session_id == "session-1"


def test_update_if_revision_stale_attempt_does_not_touch_memory_or_disk(
    tmp_path,
) -> None:
    path = tmp_path / "tickets.json"
    store = TicketStateTracker(path)
    store.set("1", TicketState(state="processed", first_seen=_now()))
    serialized_before = path.read_text(encoding="utf-8")

    commit = store.update_if_revision(
        "1",
        expected_revision=0,
        expected_state="in_silence_window",
        mutate=lambda current: current,
    )

    assert commit is None
    assert store.get("1").state == "processed"
    assert path.read_text(encoding="utf-8") == serialized_before


def test_update_if_revision_is_atomic_across_tracker_instances(tmp_path) -> None:
    """Two processes cannot both commit from the same persisted revision."""
    path = tmp_path / "tickets.json"
    seed = TicketStateTracker(path)
    seed.set("1", TicketState(state="in_silence_window", first_seen=_now()))
    first = TicketStateTracker(path)
    second = TicketStateTracker(path)
    revision = first.get("1").state_revision

    first_commit = first.update_if_revision(
        "1",
        expected_revision=revision,
        expected_state="in_silence_window",
        mutate=lambda current: replace(current, last_verdict="first"),
    )
    stale_commit = second.update_if_revision(
        "1",
        expected_revision=revision,
        expected_state="in_silence_window",
        mutate=lambda current: replace(current, last_verdict="second"),
    )

    assert first_commit is not None
    assert stale_commit is None
    persisted = TicketStateTracker(path).get("1")
    assert persisted is not None
    assert persisted.last_verdict == "first"


def test_update_if_revision_checks_owner_without_running_mutation(tmp_path) -> None:
    store = TicketStateTracker(tmp_path / "tickets.json")
    store.set(
        "1",
        TicketState(
            state="processing",
            first_seen=_now(),
            active_session_id="session-current",
        ),
    )
    before = store.get("1")
    assert before is not None
    mutation_called = False

    def mutate(current):
        nonlocal mutation_called
        mutation_called = True
        return replace(current, state="processed")

    commit = store.update_if_revision(
        "1",
        expected_revision=before.state_revision,
        expected_state="processing",
        expected_active_session_id="session-stale",
        mutate=mutate,
    )

    assert commit is None
    assert mutation_called is False
    persisted = TicketStateTracker(store.path).get("1")
    assert persisted is not None
    assert persisted.state == "processing"
    assert persisted.active_session_id == "session-current"


def test_update_if_revision_uses_detached_snapshots(tmp_path) -> None:
    store = TicketStateTracker(tmp_path / "tickets.json")
    store.set(
        "1",
        TicketState(state="in_silence_window", first_seen=_now()),
    )
    before = store.get("1")
    assert before is not None
    mutation_snapshot = None

    def mutate(current):
        nonlocal mutation_snapshot
        mutation_snapshot = current
        current.state = "processing"
        current.active_session_id = "session-1"
        return current

    commit = store.update_if_revision(
        "1",
        expected_revision=before.state_revision,
        expected_state="in_silence_window",
        mutate=mutate,
    )

    assert commit is not None
    assert mutation_snapshot is not None
    mutation_snapshot.state = "abandoned"
    commit.after.state = "processed"

    persisted = store.get("1")
    assert persisted is not None
    assert persisted.state == "processing"
    assert persisted.active_session_id == "session-1"
    assert persisted.state_revision == before.state_revision + 1


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
    store.set(
        "1",
        TicketState(
            state="in_silence_window",
            first_seen=now,
            last_comment_ts=now,
            silence_window_end=now,
        ),
    )
    store2 = TicketStateTracker(path)
    rec = store2.get("1")
    assert rec.first_seen.isoformat() == now.isoformat()
    assert rec.last_comment_ts.isoformat() == now.isoformat()


def test_form_wait_started_at_roundtrips(tmp_path):
    path = tmp_path / "tickets.json"
    started_at = _now()
    store = TicketStateTracker(path)
    store.set(
        "1",
        TicketState(
            state="waiting_for_form",
            first_seen=started_at,
            form_wait_started_at=started_at,
        ),
    )

    rec = TicketStateTracker(path).get("1")

    assert rec is not None
    assert rec.form_wait_started_at == started_at


def test_old_state_without_form_wait_started_at_loads(tmp_path):
    path = tmp_path / "tickets.json"
    path.write_text(
        '{"1":{"state":"waiting_for_form","first_seen":"2026-08-01T00:00:00+00:00"}}',
        encoding="utf-8",
    )

    rec = TicketStateTracker(path).get("1")

    assert rec is not None
    assert rec.form_wait_started_at is None


def test_corrupted_json_is_quarantined_and_reported(tmp_path):
    path = tmp_path / "tickets.json"
    path.write_text("not json at all", encoding="utf-8")

    with (
        patch("zd_agent.trigger.state_store.logger.error") as log_error,
        pytest.raises(
            TicketStateCorruptedError,
        ),
    ):
        TicketStateTracker(path)

    assert not path.exists()
    quarantined = list(tmp_path.glob("tickets.json.corrupt-*"))
    assert len(quarantined) == 1
    assert quarantined[0].read_text(encoding="utf-8") == "not json at all"
    log_error.assert_called_once()


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
    store1.set(
        "1",
        TicketState(
            state="processed",
            first_seen=_now(),
            last_agent_finish_ts=finish_ts,
        ),
    )
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


# ============================================================================
# M3 (2026-05-13) auto-public-reply 新增字段测试
# ============================================================================


class TestEscalatedToHumanState:
    """转人工后 agent 完全退出此工单。"""

    def test_escalated_to_human_is_valid_state(self):
        assert "escalated_to_human" in VALID_STATES

    def test_set_escalated_state_persists(self, tmp_path):
        """state=escalated_to_human 可以存且重读后还在。"""
        path = tmp_path / "tickets.json"
        ts = _now()
        store1 = TicketStateTracker(path)
        store1.set(
            "999",
            TicketState(
                state="escalated_to_human",
                first_seen=ts,
                escalated_at=ts,
                escalated_reason="customer_requested",
            ),
        )
        store2 = TicketStateTracker(path)
        rec = store2.get("999")
        assert rec.state == "escalated_to_human"
        assert rec.escalated_at is not None
        assert rec.escalated_at.isoformat() == ts.isoformat()
        assert rec.escalated_reason == "customer_requested"

    def test_invalid_state_still_rejected(self, tmp_path):
        """新 valid state 加了但其它无效字符串仍报错。"""
        import pytest

        store = TicketStateTracker(tmp_path / "tickets.json")
        with pytest.raises(ValueError):
            store.set(
                "1",
                TicketState(
                    state="some_invalid_state_xyz",
                    first_seen=_now(),
                ),
            )


class TestAskCount:
    """ask_count 字段：M4 用来做 10 次上限判断。"""

    def test_default_zero(self, tmp_path):
        store = TicketStateTracker(tmp_path / "tickets.json")
        store.set(
            "1",
            TicketState(
                state="waiting_for_issue",
                first_seen=_now(),
            ),
        )
        rec = store.get("1")
        assert rec.ask_count == 0

    def test_increment_and_persist(self, tmp_path):
        """模拟 trigger_consumer 每次 ask 客户累加 +1。"""
        path = tmp_path / "tickets.json"
        store1 = TicketStateTracker(path)
        store1.set(
            "1",
            TicketState(
                state="waiting_for_issue",
                first_seen=_now(),
                ask_count=3,  # 已经问了 3 次
            ),
        )
        store2 = TicketStateTracker(path)
        assert store2.get("1").ask_count == 3

    def test_high_count_supported(self, tmp_path):
        """超过 max_ask_count（10）的值也能存——M4 是逻辑层处理上限。"""
        store = TicketStateTracker(tmp_path / "tickets.json")
        store.set(
            "1",
            TicketState(
                state="escalated_to_human",
                first_seen=_now(),
                ask_count=15,
                escalated_reason="max_asks_reached",
            ),
        )
        assert store.get("1").ask_count == 15


class TestEscalatedReasonField:
    """escalated_reason 是个开放字符串，记录升级触发原因。"""

    def test_default_empty_string(self, tmp_path):
        store = TicketStateTracker(tmp_path / "tickets.json")
        store.set(
            "1",
            TicketState(
                state="waiting_for_issue",
                first_seen=_now(),
            ),
        )
        assert store.get("1").escalated_reason == ""

    def test_supports_known_reasons(self, tmp_path):
        """已支持的几种升级原因都能存。"""
        for reason in (
            "customer_requested",
            "max_asks_reached",
            "high_risk_category",
            "low_confidence",
            "agent_error",
            "manual",
        ):
            store = TicketStateTracker(tmp_path / f"{reason}.json")
            store.set(
                "1",
                TicketState(
                    state="escalated_to_human",
                    first_seen=_now(),
                    escalated_at=_now(),
                    escalated_reason=reason,
                ),
            )
            assert store.get("1").escalated_reason == reason


class TestBackwardCompat:
    """老 state.json 缺新字段时 from_dict 不应报错。"""

    def test_old_state_without_new_fields_loads(self, tmp_path):
        import json as _json

        path = tmp_path / "tickets.json"
        # 模拟旧版 state.json（没有 ask_count / escalated_at / escalated_reason）
        old_data = {
            "123": {
                "state": "waiting_for_issue",
                "first_seen": _now().isoformat(),
                "last_customer_comment_id": "abc",
            },
        }
        path.write_text(_json.dumps(old_data), encoding="utf-8")

        store = TicketStateTracker(path)
        rec = store.get("123")
        assert rec is not None
        assert rec.state == "waiting_for_issue"
        # 新字段使用默认值
        assert rec.ask_count == 0
        assert rec.escalated_at is None
        assert rec.escalated_reason == ""
