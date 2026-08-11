"""Durable v2 public-reply intent and typed ownership command store."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import secrets
import sqlite3
import time


def _effect_reference(ticket_id: int, delivery_attempt_id: str) -> str:
    digest = sha256(
        f"v1\0{ticket_id}\0{delivery_attempt_id}".encode("utf-8"),
    ).hexdigest()[:20]
    return f"casework-effect:{digest}"


@dataclass(frozen=True, slots=True)
class PendingCaseworkEffect:
    reference: str
    ticket_id: int
    delivery_attempt_id: str
    generation: int
    source_event_id: str
    reply_type: str
    body: str
    internal_analysis: str
    snapshot_customer_message_count: int | None
    snapshot_customer_last_message_id: str
    ownership_action: str
    reason_code: str
    ownership_packet: str
    public_delivery_confirmed: bool
    notification_sent: bool
    notification_ambiguous: bool
    zendesk_tag_sent: bool
    created_at: float = 0.0

    @property
    def ownership_required(self) -> bool:
        return self.ownership_action != "none"

    @property
    def ownership_complete(self) -> bool:
        return not self.ownership_required or (
            self.notification_sent and self.zendesk_tag_sent
        )

    @property
    def complete(self) -> bool:
        return self.public_delivery_confirmed and self.ownership_complete


class CaseworkEffectStore:
    def __init__(
        self,
        path: Path | str,
        *,
        claim_lease_seconds: float = 120.0,
        now_fn: Callable[[], float] = time.time,
    ) -> None:
        self.path = Path(path)
        self.claim_lease_seconds = max(1.0, claim_lease_seconds)
        self._now = now_fn
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path.parent.chmod(0o700)
        with self._connect() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS casework_effects ("
                "reference TEXT PRIMARY KEY,ticket_id INTEGER NOT NULL,"
                "delivery_attempt_id TEXT NOT NULL,generation INTEGER NOT NULL,"
                "source_event_id TEXT NOT NULL,reply_type TEXT NOT NULL,"
                "body TEXT NOT NULL,internal_analysis TEXT NOT NULL,"
                "snapshot_customer_message_count INTEGER,"
                "snapshot_customer_last_message_id TEXT NOT NULL,"
                "ownership_action TEXT NOT NULL,reason_code TEXT NOT NULL,"
                "ownership_packet TEXT NOT NULL,"
                "public_delivery_confirmed INTEGER NOT NULL DEFAULT 0,"
                "notification_sent INTEGER NOT NULL DEFAULT 0,"
                "notification_ambiguous INTEGER NOT NULL DEFAULT 0,"
                "zendesk_tag_sent INTEGER NOT NULL DEFAULT 0,"
                "created_at REAL NOT NULL,claim_token TEXT NOT NULL DEFAULT '',"
                "lease_until REAL NOT NULL DEFAULT 0)"
            )
        self.path.chmod(0o600)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def prepare(
        self,
        *,
        ticket_id: int,
        delivery_attempt_id: str,
        generation: int,
        source_event_id: str,
        reply_type: str,
        body: str,
        internal_analysis: str,
        snapshot_customer_message_count: int | None,
        snapshot_customer_last_message_id: str,
        ownership_action: str,
        reason_code: str,
        ownership_packet: str,
    ) -> PendingCaseworkEffect:
        reference = _effect_reference(ticket_id, delivery_attempt_id)
        with self._connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO casework_effects("
                "reference,ticket_id,delivery_attempt_id,generation,"
                "source_event_id,reply_type,body,internal_analysis,"
                "snapshot_customer_message_count,"
                "snapshot_customer_last_message_id,ownership_action,"
                "reason_code,ownership_packet,notification_sent,"
                "zendesk_tag_sent,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    reference,
                    ticket_id,
                    delivery_attempt_id,
                    generation,
                    source_event_id,
                    reply_type,
                    body,
                    internal_analysis,
                    snapshot_customer_message_count,
                    snapshot_customer_last_message_id,
                    ownership_action,
                    reason_code,
                    ownership_packet,
                    int(ownership_action == "none"),
                    int(ownership_action == "none"),
                    self._now(),
                ),
            )
            row = connection.execute(
                "SELECT * FROM casework_effects WHERE reference = ?",
                (reference,),
            ).fetchone()
        if row is None:
            raise RuntimeError("casework effects were not persisted")
        return _from_row(row)

    def get(self, reference: str) -> PendingCaseworkEffect | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM casework_effects WHERE reference = ?",
                (reference,),
            ).fetchone()
        return _from_row(row) if row is not None else None

    def confirm_public_delivery(self, reference: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE casework_effects SET public_delivery_confirmed = 1 "
                "WHERE reference = ?",
                (reference,),
            )
        return cursor.rowcount == 1

    def pending_ownership(self, *, limit: int = 100) -> list[PendingCaseworkEffect]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM casework_effects "
                "WHERE public_delivery_confirmed = 1 "
                "AND ownership_action != 'none' "
                "AND (notification_sent = 0 OR zendesk_tag_sent = 0) "
                "ORDER BY created_at LIMIT ?",
                (max(1, limit),),
            ).fetchall()
        return [_from_row(row) for row in rows]

    def claim_ownership(self, reference: str) -> str | None:
        now = self._now()
        token = secrets.token_hex(16)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                "UPDATE casework_effects SET claim_token = ?, lease_until = ? "
                "WHERE reference = ? AND public_delivery_confirmed = 1 "
                "AND ownership_action != 'none' "
                "AND (notification_sent = 0 OR zendesk_tag_sent = 0) "
                "AND (claim_token = '' OR lease_until <= ?)",
                (token, now + self.claim_lease_seconds, reference, now),
            )
        return token if cursor.rowcount == 1 else None

    def renew(self, reference: str, token: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE casework_effects SET lease_until = ? "
                "WHERE reference = ? AND claim_token = ?",
                (self._now() + self.claim_lease_seconds, reference, token),
            )
        return cursor.rowcount == 1

    def release(self, reference: str, token: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE casework_effects SET claim_token = '', lease_until = 0 "
                "WHERE reference = ? AND claim_token = ?",
                (reference, token),
            )

    def mark_ownership_effect(
        self,
        reference: str,
        token: str,
        column: str,
    ) -> bool:
        if column not in {"notification_sent", "zendesk_tag_sent"}:
            raise ValueError(f"unsupported ownership effect: {column}")
        with self._connect() as connection:
            cursor = connection.execute(
                f"UPDATE casework_effects SET {column} = 1, "
                "notification_ambiguous = CASE "
                "WHEN ? = 'notification_sent' THEN 0 "
                "ELSE notification_ambiguous END "
                "WHERE reference = ? AND claim_token = ?",
                (column, reference, token),
            )
        return cursor.rowcount == 1

    def begin_notification(self, reference: str, token: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE casework_effects SET notification_ambiguous = 1 "
                "WHERE reference = ? AND claim_token = ?",
                (reference, token),
            )
        return cursor.rowcount == 1

    def clear_notification_ambiguous(self, reference: str, token: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE casework_effects SET notification_ambiguous = 0 "
                "WHERE reference = ? AND claim_token = ? "
                "AND notification_sent = 0",
                (reference, token),
            )
        return cursor.rowcount == 1


def _from_row(row: sqlite3.Row) -> PendingCaseworkEffect:
    return PendingCaseworkEffect(
        reference=str(row["reference"]),
        ticket_id=int(row["ticket_id"]),
        delivery_attempt_id=str(row["delivery_attempt_id"]),
        generation=int(row["generation"]),
        source_event_id=str(row["source_event_id"]),
        reply_type=str(row["reply_type"]),
        body=str(row["body"]),
        internal_analysis=str(row["internal_analysis"]),
        snapshot_customer_message_count=(
            int(row["snapshot_customer_message_count"])
            if row["snapshot_customer_message_count"] is not None
            else None
        ),
        snapshot_customer_last_message_id=str(
            row["snapshot_customer_last_message_id"],
        ),
        ownership_action=str(row["ownership_action"]),
        reason_code=str(row["reason_code"]),
        ownership_packet=str(row["ownership_packet"]),
        public_delivery_confirmed=bool(row["public_delivery_confirmed"]),
        notification_sent=bool(row["notification_sent"]),
        notification_ambiguous=bool(row["notification_ambiguous"]),
        zendesk_tag_sent=bool(row["zendesk_tag_sent"]),
        created_at=float(row["created_at"]),
    )
