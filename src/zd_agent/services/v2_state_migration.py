"""One-way, idempotent migration of active ticket lifecycle rows to v2."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from functools import partial
from pathlib import Path

from zd_agent.services.atomic_write import atomic_write_text
from zd_agent.services.public_reply_delivery_store import (
    PublicReplyDeliveryAttemptState,
    PublicReplyDeliveryStateError,
    PublicReplyDeliveryStore,
)
from zd_agent.trigger.state_store import TicketState, TicketStateTracker


_V2_SILENCE_VERDICTS = {
    "agent_deferred",
    "agent_retry",
    "casework_ready",
    "ownership_retry",
}


@dataclass(frozen=True)
class V2StateMigrationReport:
    migrated: int
    backup_path: Path | None


def _backup_before_first_change(state: TicketStateTracker) -> Path:
    backup = state.path.with_name("tickets.pre-v2.json")
    if not backup.exists():
        atomic_write_text(backup, state.path.read_text(encoding="utf-8"))
        backup.chmod(0o600)
    return backup


def _require_record(record: TicketState | None) -> TicketState:
    if record is None:
        raise RuntimeError("v2 state migration requires an existing record")
    return record


def _migrate_confirmed_waiting(
    record: TicketState | None,
    *,
    delivered_ask: PublicReplyDeliveryAttemptState,
) -> TicketState:
    record = _require_record(record)
    record.state = "awaiting_customer"
    record.last_verdict = "legacy_confirmed_ask_migrated"
    record.active_session_id = ""
    record.silence_window_end = None
    record.last_agent_finish_ts = record.nudge_sent_at or datetime.fromtimestamp(
        delivered_ask.created_at,
        tz=timezone.utc,
    )
    return record


def _migrate_unconfirmed_waiting(
    record: TicketState | None,
    *,
    ticket_id: str,
    now: datetime,
) -> TicketState:
    """Schedule a fresh v2 assessment when no customer-visible ask is proven."""
    record = _require_record(record)
    record.state = "in_silence_window"
    record.last_verdict = "casework_ready"
    record.active_session_id = ""
    record.silence_window_end = now
    record.last_agent_finish_ts = None
    record.delivery_attempt_id = f"v2-migration:{ticket_id}:{record.state_revision + 1}"
    return record


def _migrate_ambiguous_waiting(record: TicketState | None) -> TicketState:
    """Quarantine a possible external write until an operator reconciles it."""
    record = _require_record(record)
    record.state = "manual_reconciliation"
    record.last_verdict = "public_reply_delivery_requires_reconciliation"
    record.active_session_id = ""
    record.silence_window_end = None
    record.last_agent_finish_ts = None
    return record


class _WaitingDeliveryDisposition(str, Enum):
    CONFIRMED_ASK = "confirmed_ask"
    DEFINITELY_UNSENT = "definitely_unsent"
    MANUAL_RECONCILIATION = "manual_reconciliation"


def _waiting_delivery_disposition(
    state: TicketStateTracker,
    *,
    ticket_id: int,
    record: TicketState,
) -> tuple[_WaitingDeliveryDisposition, PublicReplyDeliveryAttemptState | None]:
    if not record.delivery_attempt_id:
        return _WaitingDeliveryDisposition.DEFINITELY_UNSENT, None
    path = state.path.parent / "public_reply_outbox.sqlite3"
    if not path.exists():
        return _WaitingDeliveryDisposition.DEFINITELY_UNSENT, None
    try:
        attempt = PublicReplyDeliveryStore(path).inspect_logical_attempt(
            delivery_attempt_id=record.delivery_attempt_id,
            ticket_id=ticket_id,
        )
    except PublicReplyDeliveryStateError:
        return _WaitingDeliveryDisposition.MANUAL_RECONCILIATION, None
    if not attempt.exists:
        return _WaitingDeliveryDisposition.DEFINITELY_UNSENT, attempt
    if attempt.delivered and attempt.reply_type == "ask":
        return _WaitingDeliveryDisposition.CONFIRMED_ASK, attempt
    if attempt.delivered or attempt.ambiguous or attempt.active:
        return _WaitingDeliveryDisposition.MANUAL_RECONCILIATION, attempt
    return _WaitingDeliveryDisposition.DEFINITELY_UNSENT, attempt


def _migrate_silence(record: TicketState | None) -> TicketState:
    record = _require_record(record)
    record.last_verdict = "casework_ready"
    record.active_session_id = ""
    return record


def _migrate_form_wait(record: TicketState | None) -> TicketState:
    record = _require_record(record)
    record.form_wait_started_at = record.last_eval_at or record.first_seen
    return record


def migrate_ticket_state_to_v2(
    state: TicketStateTracker,
    *,
    now: datetime | None = None,
) -> V2StateMigrationReport:
    """Normalize live legacy rows without reopening historical terminal cases."""
    candidates = [
        (ticket_id, record)
        for ticket_id, record in state.all().items()
        if record.state == "waiting_for_issue"
        or (record.state == "waiting_for_form" and record.form_wait_started_at is None)
        or (
            record.state == "in_silence_window"
            and record.last_verdict not in _V2_SILENCE_VERDICTS
        )
    ]
    if not candidates:
        existing_backup = state.path.with_name("tickets.pre-v2.json")
        return V2StateMigrationReport(
            migrated=0,
            backup_path=existing_backup if existing_backup.exists() else None,
        )

    backup = _backup_before_first_change(state)
    migration_time = now or datetime.now(timezone.utc)
    migrated = 0
    for ticket_id, record in candidates:
        if record.state == "waiting_for_issue":
            disposition, delivered_ask = _waiting_delivery_disposition(
                state,
                ticket_id=int(ticket_id),
                record=record,
            )
            if disposition is _WaitingDeliveryDisposition.CONFIRMED_ASK:
                if delivered_ask is None:
                    raise RuntimeError("confirmed ask migration lost delivery evidence")
                mutate = partial(
                    _migrate_confirmed_waiting,
                    delivered_ask=delivered_ask,
                )
            elif disposition is _WaitingDeliveryDisposition.MANUAL_RECONCILIATION:
                mutate = _migrate_ambiguous_waiting
            else:
                mutate = partial(
                    _migrate_unconfirmed_waiting,
                    ticket_id=ticket_id,
                    now=migration_time,
                )
        elif record.state == "waiting_for_form":
            mutate = _migrate_form_wait
        else:
            mutate = _migrate_silence
        commit = state.update_if_revision(
            ticket_id,
            expected_revision=record.state_revision,
            expected_state=record.state,
            mutate=mutate,
        )
        migrated += int(commit is not None)
    return V2StateMigrationReport(migrated=migrated, backup_path=backup)
