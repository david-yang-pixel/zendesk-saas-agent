from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json

from zd_agent.services.v2_state_migration import migrate_ticket_state_to_v2
from zd_agent.services.public_reply_delivery_store import PublicReplyDeliveryStore
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

    report = migrate_ticket_state_to_v2(state, now=NOW)

    assert report.migrated == 2
    assert report.backup_path == tmp_path / "tickets.pre-v2.json"
    assert report.backup_path.read_text(encoding="utf-8") == original
    assert state.get(1001).state == "in_silence_window"
    assert state.get(1001).last_customer_comment_id == "comment-1"
    assert state.get(1001).last_verdict == "casework_ready"
    assert state.get(1001).silence_window_end == NOW
    assert state.get(1001).delivery_attempt_id.startswith("v2-migration:1001:")
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


def test_migration_preserves_wait_only_for_a_confirmed_delivered_ask(tmp_path) -> None:
    path = tmp_path / "tickets.json"
    state = TicketStateTracker(path)
    state.set(
        1001,
        TicketState(
            state="waiting_for_issue",
            first_seen=NOW - timedelta(days=3),
            delivery_attempt_id="legacy-ask-1",
            last_verdict="need_more_info",
        ),
    )
    outbox = PublicReplyDeliveryStore(
        tmp_path / "public_reply_outbox.sqlite3",
        now_fn=lambda: NOW.timestamp(),
    )
    attempt = outbox.prepare(
        delivery_attempt_id="legacy-ask-1",
        ticket_id=1001,
        reply_type="ask",
        body="Please provide the missing error timestamp.",
    )
    token = outbox.claim(attempt.delivery_key)
    assert token is not None
    assert outbox.begin_send(attempt.delivery_key, token) is True
    assert outbox.mark_delivered(attempt.delivery_key, token) is True

    report = migrate_ticket_state_to_v2(state, now=NOW)

    migrated = state.get(1001)
    assert report.migrated == 1
    assert migrated is not None
    assert migrated.state == "awaiting_customer"
    assert migrated.last_verdict == "legacy_confirmed_ask_migrated"
    assert migrated.last_agent_finish_ts is not None
    assert migrated.last_agent_finish_ts == NOW


def test_migration_reassesses_a_definitely_unsent_ask(tmp_path) -> None:
    path = tmp_path / "tickets.json"
    state = TicketStateTracker(path)
    state.set(
        1001,
        TicketState(
            state="waiting_for_issue",
            first_seen=NOW - timedelta(days=3),
            delivery_attempt_id="ambiguous-ask-1",
        ),
    )
    PublicReplyDeliveryStore(tmp_path / "public_reply_outbox.sqlite3").prepare(
        delivery_attempt_id="ambiguous-ask-1",
        ticket_id=1001,
        reply_type="ask",
        body="Please provide details.",
    )

    migrate_ticket_state_to_v2(state, now=NOW)

    migrated = state.get(1001)
    assert migrated is not None
    assert migrated.state == "in_silence_window"
    assert migrated.silence_window_end == NOW
    assert migrated.delivery_attempt_id.startswith("v2-migration:1001:")


def test_migration_quarantines_an_ambiguous_ask_without_rotating_attempt(
    tmp_path,
) -> None:
    path = tmp_path / "tickets.json"
    state = TicketStateTracker(path)
    state.set(
        1001,
        TicketState(
            state="waiting_for_issue",
            first_seen=NOW - timedelta(days=3),
            delivery_attempt_id="ambiguous-ask-1",
        ),
    )
    outbox = PublicReplyDeliveryStore(
        tmp_path / "public_reply_outbox.sqlite3",
        now_fn=lambda: NOW.timestamp(),
    )
    attempt = outbox.prepare(
        delivery_attempt_id="ambiguous-ask-1",
        ticket_id=1001,
        reply_type="ask",
        body="Please provide details.",
    )
    token = outbox.claim(attempt.delivery_key)
    assert token is not None
    assert outbox.begin_send(attempt.delivery_key, token) is True

    migrate_ticket_state_to_v2(state, now=NOW)

    migrated = state.get(1001)
    assert migrated is not None
    assert migrated.state == "manual_reconciliation"
    assert migrated.delivery_attempt_id == "ambiguous-ask-1"
    assert migrated.last_verdict == "public_reply_delivery_requires_reconciliation"


def test_migration_quarantines_an_active_public_reply_lease(tmp_path) -> None:
    path = tmp_path / "tickets.json"
    state = TicketStateTracker(path)
    state.set(
        1001,
        TicketState(
            state="waiting_for_issue",
            first_seen=NOW,
            delivery_attempt_id="active-ask-1",
        ),
    )
    outbox = PublicReplyDeliveryStore(tmp_path / "public_reply_outbox.sqlite3")
    attempt = outbox.prepare(
        delivery_attempt_id="active-ask-1",
        ticket_id=1001,
        reply_type="ask",
        body="Please provide details.",
    )
    assert outbox.claim(attempt.delivery_key) is not None

    migrate_ticket_state_to_v2(state, now=NOW)

    migrated = state.get(1001)
    assert migrated is not None
    assert migrated.state == "manual_reconciliation"
    assert migrated.delivery_attempt_id == "active-ask-1"


def test_migration_without_legacy_active_state_does_not_create_backup(tmp_path) -> None:
    state = TicketStateTracker(tmp_path / "tickets.json")
    state.set(1001, TicketState(state="processed", first_seen=NOW))

    report = migrate_ticket_state_to_v2(state)

    assert report.migrated == 0
    assert report.backup_path is None
    assert not (tmp_path / "tickets.pre-v2.json").exists()


def test_migration_backfills_form_wait_start_from_legacy_last_eval_at(tmp_path) -> None:
    path = tmp_path / "tickets.json"
    first_seen = NOW - timedelta(days=30)
    form_wait_started_at = NOW - timedelta(minutes=5)
    path.write_text(
        json.dumps(
            {
                "1001": {
                    "state": "waiting_for_form",
                    "first_seen": first_seen.isoformat(),
                    "last_eval_at": form_wait_started_at.isoformat(),
                    "last_verdict": "waiting_for_form",
                },
            },
        ),
        encoding="utf-8",
    )
    original = path.read_text(encoding="utf-8")
    state = TicketStateTracker(path)

    report = migrate_ticket_state_to_v2(state)

    migrated = state.get(1001)
    assert report.migrated == 1
    assert report.backup_path is not None
    assert report.backup_path.read_text(encoding="utf-8") == original
    assert migrated is not None
    assert migrated.first_seen == first_seen
    assert migrated.form_wait_started_at == form_wait_started_at
