"""Crash-safe Feishu notification outbox for session outcomes."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
import secrets
import sqlite3
import time
from typing import Any

from zd_agent.integrations.notification_service import push_notification_card
from zd_agent.log import get_logger

logger = get_logger(module="services.notification_outbox")

PushNotification = Callable[..., Awaitable[dict[str, Any]]]


@dataclass(frozen=True, slots=True)
class PendingNotification:
    reference: str
    ticket_id: int
    card_type: str
    title: str
    content: str
    zendesk_url: str
    urgent: bool
    attempts: int = 0
    last_error: str = ""
    claim_token: str = ""


class NotificationOutbox:
    """Persist one immutable notification payload until Feishu confirms it."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path.parent.chmod(0o700)
        with self._connect() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS notifications ("
                "reference TEXT PRIMARY KEY,ticket_id INTEGER NOT NULL,"
                "card_type TEXT NOT NULL,title TEXT NOT NULL,content TEXT NOT NULL,"
                "zendesk_url TEXT NOT NULL,urgent INTEGER NOT NULL,"
                "sent INTEGER NOT NULL DEFAULT 0,attempts INTEGER NOT NULL DEFAULT 0,"
                "last_error TEXT NOT NULL DEFAULT '',created_at REAL NOT NULL,"
                "updated_at REAL NOT NULL,claim_token TEXT NOT NULL DEFAULT '',"
                "lease_until REAL NOT NULL DEFAULT 0)"
            )
            columns = {
                str(row[1])
                for row in connection.execute(
                    "PRAGMA table_info(notifications)"
                ).fetchall()
            }
            if "claim_token" not in columns:
                connection.execute(
                    "ALTER TABLE notifications ADD COLUMN "
                    "claim_token TEXT NOT NULL DEFAULT ''"
                )
            if "lease_until" not in columns:
                connection.execute(
                    "ALTER TABLE notifications ADD COLUMN "
                    "lease_until REAL NOT NULL DEFAULT 0"
                )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _row(row: sqlite3.Row) -> PendingNotification:
        return PendingNotification(
            reference=str(row["reference"]),
            ticket_id=int(row["ticket_id"]),
            card_type=str(row["card_type"]),
            title=str(row["title"]),
            content=str(row["content"]),
            zendesk_url=str(row["zendesk_url"]),
            urgent=bool(row["urgent"]),
            attempts=int(row["attempts"]),
            last_error=str(row["last_error"]),
            claim_token=str(row["claim_token"]),
        )

    def prepare(
        self,
        *,
        reference: str,
        ticket_id: int,
        card_type: str,
        title: str,
        content: str,
        zendesk_url: str,
        urgent: bool,
    ) -> PendingNotification:
        """Insert once; later calls with the same reference cannot rewrite it."""
        if not reference.strip() or ticket_id <= 0 or not content.strip():
            raise ValueError("notification requires reference, ticket, and content")
        now = time.time()
        with self._connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO notifications "
                "(reference,ticket_id,card_type,title,content,zendesk_url,urgent,"
                "created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    reference,
                    ticket_id,
                    card_type,
                    title,
                    content,
                    zendesk_url,
                    int(urgent),
                    now,
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM notifications WHERE reference = ?",
                (reference,),
            ).fetchone()
        if row is None:  # pragma: no cover - SQLite invariant.
            raise RuntimeError("prepared notification could not be read")
        return self._row(row)

    def pending(self, *, limit: int = 20) -> list[PendingNotification]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM notifications WHERE sent = 0 "
                "ORDER BY created_at, reference LIMIT ?",
                (max(1, int(limit)),),
            ).fetchall()
        return [self._row(row) for row in rows]

    def claim_pending(self, *, limit: int = 20) -> list[PendingNotification]:
        """Lease ready rows atomically so parallel reconcilers cannot double-send."""
        now = time.time()
        token = secrets.token_hex(16)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            references = [
                str(row["reference"])
                for row in connection.execute(
                    "SELECT reference FROM notifications WHERE sent = 0 "
                    "AND lease_until <= ? ORDER BY created_at,reference LIMIT ?",
                    (now, max(1, int(limit))),
                ).fetchall()
            ]
            if not references:
                return []
            placeholders = ",".join("?" for _ in references)
            connection.execute(
                f"UPDATE notifications SET claim_token = ?,lease_until = ?,"  # noqa: S608
                f"updated_at = ? WHERE reference IN ({placeholders}) "
                "AND sent = 0 AND lease_until <= ?",
                (token, now + 120.0, now, *references, now),
            )
            rows = connection.execute(
                f"SELECT * FROM notifications WHERE claim_token = ? "  # noqa: S608
                f"AND reference IN ({placeholders}) ORDER BY created_at,reference",
                (token, *references),
            ).fetchall()
        return [self._row(row) for row in rows]

    def mark_sent(self, reference: str, claim_token: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE notifications SET sent = 1,claim_token = '',lease_until = 0,"
                "updated_at = ? WHERE reference = ? AND claim_token = ?",
                (time.time(), reference, claim_token),
            )

    def mark_failed(self, reference: str, claim_token: str, error: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE notifications SET attempts = attempts + 1,last_error = ?,"
                "claim_token = '',lease_until = 0,updated_at = ? "
                "WHERE reference = ? AND sent = 0 AND claim_token = ?",
                (error[:500], time.time(), reference, claim_token),
            )


async def reconcile_notifications(
    *,
    store: NotificationOutbox,
    push: PushNotification = push_notification_card,
    limit: int = 20,
) -> int:
    """Attempt pending cards; stable references make repeated calls idempotent."""
    sent = 0
    for notice in store.claim_pending(limit=limit):
        try:
            payload = await push(
                card_type=notice.card_type,
                ticket_id=str(notice.ticket_id),
                content=notice.content,
                title=notice.title,
                zendesk_url=notice.zendesk_url,
                urgent=notice.urgent,
                idempotency_reference=notice.reference,
            )
        except Exception as error:  # noqa: BLE001 - durable retry boundary.
            store.mark_failed(
                notice.reference,
                notice.claim_token,
                f"{type(error).__name__}: {error}",
            )
            logger.warning(
                "notification_push_failed",
                ticket_id=notice.ticket_id,
                reference=notice.reference,
                error_type=type(error).__name__,
            )
            continue
        if not payload.get("success"):
            store.mark_failed(
                notice.reference,
                notice.claim_token,
                str(payload.get("error") or payload.get("code") or "unknown error"),
            )
            continue
        store.mark_sent(notice.reference, notice.claim_token)
        sent += 1
        logger.info(
            "notification_push_confirmed",
            ticket_id=notice.ticket_id,
            reference=notice.reference,
        )
    return sent
