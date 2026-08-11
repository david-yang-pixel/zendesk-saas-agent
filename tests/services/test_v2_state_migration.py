from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json

from zd_agent.services.v2_state_migration import migrate_ticket_state_to_v2
from zd_agent.trigger.state_store import TicketState, TicketStateTracker


NOW = datetime(2026, 8, 11, 8, 0, tzinfo=timezone.utc)


def test_migration_preserves_backup_and_normalizes_only_active_legacy_states(
    tmp_path,
) -> None:
    path = tmp_path / "tickets.json"
    state = TicketStateTracker(path)
    state.set(
        1001,
        TicketState(
            state="waiting_for_issue",
            first_seen=NOW - timedelta(hours=1),
            last_customer_comment_id="comment-1",
            last_verdict="greeting",
        ),
    )
    state.set(
        1002,
        TicketState(
            state="in_silence_window",
            first_seen=NOW - timedelta(minutes=5),
            silence_window_end=NOW + timedelta(seconds=10),
            last_verdict="need_more_info",
        ),
    )
    state.set(
        1003,
        TicketState(
            state="escalated_to_human",
            first_seen=NOW - timedelta(days=2),
            escalated_reason="historical",
        ),
    )
    original = path.read_text(encoding="utf-8")

    report = migrate_ticket_state_to_v2(state)

    assert report.migrated == 2
    assert report.backup_path == tmp_path / "tickets.pre-v2.json"
    assert report.backup_path.read_text(encoding="utf-8") == original
    assert state.get(1001).state == "awaiting_customer"
    assert state.get(1001).last_customer_comment_id == "comment-1"
    assert state.get(1001).last_verdict == "legacy_waiting_migrated"
    assert state.get(1002).state == "in_silence_window"
    assert state.get(1002).last_verdict == "casework_ready"
    assert state.get(1003).state == "escalated_to_human"
    assert state.get(1003).escalated_reason == "historical"


def test_migration_is_idempotent_and_never_replaces_original_backup(tmp_path) -> None:
    path = tmp_path / "tickets.json"
    state = TicketStateTracker(path)
    state.set(
        1001,
        TicketState(
            state="waiting_for_issue",
            first_seen=NOW,
        ),
    )

    first = migrate_ticket_state_to_v2(state)
    backup = first.backup_path.read_text(encoding="utf-8")
    first_revision = state.get(1001).state_revision
    second = migrate_ticket_state_to_v2(state)

    assert first.migrated == 1
    assert second.migrated == 0
    assert state.get(1001).state_revision == first_revision
    assert second.backup_path.read_text(encoding="utf-8") == backup
    assert json.loads(backup)["1001"]["state"] == "waiting_for_issue"


def test_migration_without_legacy_active_state_does_not_create_backup(tmp_path) -> None:
    state = TicketStateTracker(tmp_path / "tickets.json")
    state.set(1001, TicketState(state="processed", first_seen=NOW))

    report = migrate_ticket_state_to_v2(state)

    assert report.migrated == 0
    assert report.backup_path is None
    assert not (tmp_path / "tickets.pre-v2.json").exists()
