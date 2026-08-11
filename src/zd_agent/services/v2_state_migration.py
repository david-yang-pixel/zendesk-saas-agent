"""One-way, idempotent migration of active ticket lifecycle rows to v2."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from zd_agent.services.atomic_write import atomic_write_text
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


def _migrate_waiting(record: TicketState) -> TicketState:
    record.state = "awaiting_customer"
    record.last_verdict = "legacy_waiting_migrated"
    record.active_session_id = ""
    record.silence_window_end = None
    return record


def _migrate_silence(record: TicketState) -> TicketState:
    record.last_verdict = "casework_ready"
    record.active_session_id = ""
    return record


def migrate_ticket_state_to_v2(
    state: TicketStateTracker,
) -> V2StateMigrationReport:
    """Normalize live legacy rows without reopening historical terminal cases."""
    candidates = [
        (ticket_id, record)
        for ticket_id, record in state.all().items()
        if record.state == "waiting_for_issue"
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
    migrated = 0
    for ticket_id, record in candidates:
        mutate = (
            _migrate_waiting
            if record.state == "waiting_for_issue"
            else _migrate_silence
        )
        commit = state.update_if_revision(
            ticket_id,
            expected_revision=record.state_revision,
            expected_state=record.state,
            mutate=mutate,
        )
        migrated += int(commit is not None)
    return V2StateMigrationReport(migrated=migrated, backup_path=backup)
