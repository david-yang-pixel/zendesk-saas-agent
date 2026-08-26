from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from zd_agent.services.durable_trigger_queue_sql import (
    CREATE_READY_INDEX_SQL,
    CREATE_TABLE_SQL,
    DELETE_CLAIM_SQL,
    DELETE_DEAD_LETTER_SQL,
    INSERT_SQL,
    MARK_CORRUPT_SQL,
    MARK_DEAD_LETTER_RECONCILED_SQL,
    MARK_FAILED_SQL,
    MARK_PROCESSING_SQL,
    RECOVER_PROCESSING_SQL,
    RELEASE_SQL,
    REVIVE_DEAD_LETTER_SQL,
    REVIVE_RECOVERY_SQL,
    SELECT_DEAD_LETTERS_SQL,
    SELECT_NEXT_SQL,
    SELECT_READY_SQL,
)


class DurableTriggerStore:
    def __init__(
        self,
        path: Path | str,
        *,
        lease_seconds: float,
        now_fn: Callable[[], float],
    ) -> None:
        self.path = Path(path)
        self.lease_seconds = lease_seconds
        self._now = now_fn
        self.initialize()

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path.parent.chmod(0o700)
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        self.restrict_file_permissions()
        return connection

    def restrict_file_permissions(self) -> None:
        self.path.parent.chmod(0o700)
        for candidate in (
            self.path,
            Path(f"{self.path}-wal"),
            Path(f"{self.path}-shm"),
        ):
            try:
                candidate.chmod(0o600)
            except FileNotFoundError:
                continue

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.execute(CREATE_TABLE_SQL)
            columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(trigger_events)")
            }
            if "claim_token" not in columns:
                connection.execute(
                    "ALTER TABLE trigger_events "
                    "ADD COLUMN claim_token TEXT NOT NULL DEFAULT ''"
                )
            if "lease_until" not in columns:
                connection.execute(
                    "ALTER TABLE trigger_events "
                    "ADD COLUMN lease_until REAL NOT NULL DEFAULT 0"
                )
            if "deferrals" not in columns:
                connection.execute(
                    "ALTER TABLE trigger_events "
                    "ADD COLUMN deferrals INTEGER NOT NULL DEFAULT 0"
                )
            connection.execute(CREATE_READY_INDEX_SQL)
            now = self._now()
            connection.execute(RECOVER_PROCESSING_SQL, (now, now))
        self.restrict_file_permissions()

    def revive_dead_letter(
        self,
        event_key: str,
        payload: dict[str, Any],
        available_at: float,
    ) -> bool:
        now = self._now()
        with self.connect() as connection:
            cursor = connection.execute(
                REVIVE_DEAD_LETTER_SQL,
                (
                    json.dumps(payload, ensure_ascii=False, default=str),
                    available_at,
                    now,
                    event_key,
                ),
            )
            return cursor.rowcount == 1

    def revive_recovery(
        self,
        event_key: str,
        payload: dict[str, Any],
        available_at: float,
    ) -> bool:
        """Revive an audited recovery row for the same deterministic generation."""
        now = self._now()
        with self.connect() as connection:
            cursor = connection.execute(
                REVIVE_RECOVERY_SQL,
                (
                    json.dumps(payload, ensure_ascii=False, default=str),
                    available_at,
                    now,
                    event_key,
                ),
            )
            return cursor.rowcount == 1

    def insert(
        self,
        event_key: str,
        payload: dict[str, Any],
        available_at: float,
    ) -> bool:
        now = self._now()
        with self.connect() as connection:
            cursor = connection.execute(
                INSERT_SQL,
                (
                    event_key,
                    json.dumps(payload, ensure_ascii=False, default=str),
                    available_at,
                    now,
                    now,
                ),
            )
            return cursor.rowcount == 1

    def claim_ready(self) -> tuple[dict[str, Any] | None, float | None]:
        now = self._now()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(RECOVER_PROCESSING_SQL, (now, now))
            while True:
                row = connection.execute(SELECT_READY_SQL, (now,)).fetchone()
                if row is None:
                    break
                try:
                    payload = json.loads(row["payload"])
                except (json.JSONDecodeError, UnicodeDecodeError, TypeError):
                    connection.execute(MARK_CORRUPT_SQL, (now, row["id"]))
                    continue
                if not isinstance(payload, dict):
                    connection.execute(MARK_CORRUPT_SQL, (now, row["id"]))
                    continue
                claim_token = uuid.uuid4().hex
                claimed = connection.execute(
                    MARK_PROCESSING_SQL,
                    (
                        claim_token,
                        now + self.lease_seconds,
                        now,
                        row["id"],
                    ),
                )
                if claimed.rowcount != 1:
                    continue
                payload["_queue_event_id"] = int(row["id"])
                payload["_queue_key"] = row["event_key"]
                payload["_queue_attempts"] = int(row["attempts"])
                payload["_queue_deferrals"] = int(row["deferrals"])
                payload["_queue_claim_token"] = claim_token
                return payload, None

            next_row = connection.execute(SELECT_NEXT_SQL).fetchone()
            value = next_row["next_available_at"] if next_row else None
            return None, float(value) if value is not None else None

    def reschedule(
        self,
        event_id: int,
        claim_token: str,
        delay_seconds: float,
        reason: str,
        *,
        increment_deferrals: bool = False,
        payload: dict[str, Any] | None = None,
    ) -> bool:
        now = self._now()
        serialized_payload = json.dumps(
            _persisted_payload(payload or {}),
            ensure_ascii=False,
            default=str,
        )
        with self.connect() as connection:
            if increment_deferrals:
                cursor = connection.execute(
                    "UPDATE trigger_events SET payload = ?, status = 'pending', "
                    "available_at = ?, deferrals = deferrals + 1, "
                    "last_error = ?, claim_token = '', lease_until = 0, "
                    "updated_at = ? WHERE id = ? AND status = 'processing' "
                    "AND claim_token = ?",
                    (
                        serialized_payload,
                        now + delay_seconds,
                        reason[:500],
                        now,
                        event_id,
                        claim_token,
                    ),
                )
            else:
                cursor = connection.execute(
                    RELEASE_SQL,
                    (
                        serialized_payload,
                        now + delay_seconds,
                        reason[:500],
                        now,
                        event_id,
                        claim_token,
                    ),
                )
            return cursor.rowcount == 1

    def delete(self, event_id: int, claim_token: str) -> bool:
        with self.connect() as connection:
            cursor = connection.execute(
                DELETE_CLAIM_SQL,
                (event_id, claim_token),
            )
            return cursor.rowcount == 1

    def mark_failed(
        self,
        event_id: int,
        claim_token: str,
        attempts: int,
        pending: bool,
        delay: float,
        error: str,
        payload: dict[str, Any] | None = None,
    ) -> bool:
        now = self._now()
        with self.connect() as connection:
            cursor = connection.execute(
                MARK_FAILED_SQL,
                (
                    json.dumps(
                        _persisted_payload(payload or {}),
                        ensure_ascii=False,
                        default=str,
                    ),
                    "pending" if pending else "dead_letter",
                    attempts,
                    now + delay,
                    str(error)[:500],
                    now,
                    event_id,
                    claim_token,
                ),
            )
            return cursor.rowcount == 1

    def count(self, status: str) -> int:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS count FROM trigger_events WHERE status = ?",
                (status,),
            ).fetchone()
            return int(row["count"] if row else 0)

    def discard_backlog(self, reason: str) -> int:
        """Discard all not-yet-owned triggers while retaining an audit row."""
        now = self._now()
        with self.connect() as connection:
            cursor = connection.execute(
                "UPDATE trigger_events SET status = 'discarded', "
                "last_error = ?, claim_token = '', lease_until = 0, updated_at = ? "
                "WHERE status IN ('pending','processing','dead_letter')",
                (str(reason)[:500], now),
            )
            return max(0, int(cursor.rowcount))

    def dead_letters(self, limit: int) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                SELECT_DEAD_LETTERS_SQL,
                (limit,),
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            payload: dict[str, Any]
            try:
                payload = json.loads(row["payload"])
            except (json.JSONDecodeError, UnicodeDecodeError, TypeError):
                payload = {"_queue_corrupt_payload": True}
            if not isinstance(payload, dict):
                payload = {"_queue_corrupt_payload": True}
            payload["_queue_event_id"] = int(row["id"])
            payload["_queue_key"] = row["event_key"]
            payload["_queue_attempts"] = int(row["attempts"])
            payload["_queue_deferrals"] = int(row["deferrals"])
            payload["_queue_error"] = row["last_error"]
            result.append(payload)
        return result

    def resolve_dead_letter(self, event_id: int) -> bool:
        """Retain the audit row while removing it from reconciliation scans."""
        with self.connect() as connection:
            cursor = connection.execute(
                MARK_DEAD_LETTER_RECONCILED_SQL,
                (self._now(), event_id),
            )
            return cursor.rowcount == 1

    def discard_dead_letter(self, event_id: int) -> bool:
        """Remove a no-write discovery hint so its fallback can reuse the key."""
        with self.connect() as connection:
            cursor = connection.execute(DELETE_DEAD_LETTER_SQL, (event_id,))
            return cursor.rowcount == 1


def _persisted_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in payload.items()
        if key
        not in {
            "_queue_event_id",
            "_queue_claim_token",
            "_queue_attempts",
            "_queue_deferrals",
            "_queue_error",
        }
    }
