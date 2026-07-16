from __future__ import annotations

import hashlib
import secrets
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class PublicReplyDeliveryAttempt:
    delivery_key: str
    body: str
    created_at: float
    delivered: bool


class PublicReplyDeliveryStateError(RuntimeError):
    pass


class PublicReplyDeliveryStore:
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
                "CREATE TABLE IF NOT EXISTS public_reply_deliveries ("
                "delivery_key TEXT PRIMARY KEY,"
                "body TEXT NOT NULL,"
                "created_at REAL NOT NULL,"
                "delivered_at REAL,"
                "claim_token TEXT NOT NULL DEFAULT '',"
                "lease_until REAL NOT NULL DEFAULT 0)"
            )
            columns = {
                str(row["name"])
                for row in connection.execute(
                    "PRAGMA table_info(public_reply_deliveries)"
                )
            }
            if "claim_token" not in columns:
                connection.execute(
                    "ALTER TABLE public_reply_deliveries "
                    "ADD COLUMN claim_token TEXT NOT NULL DEFAULT ''"
                )
            if "lease_until" not in columns:
                connection.execute(
                    "ALTER TABLE public_reply_deliveries "
                    "ADD COLUMN lease_until REAL NOT NULL DEFAULT 0"
                )
        self.path.chmod(0o600)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def prepare(
        self,
        *,
        delivery_attempt_id: str,
        ticket_id: int,
        reply_type: str,
        body: str,
    ) -> PublicReplyDeliveryAttempt:
        material = f"v1\0{delivery_attempt_id}\0{ticket_id}\0{reply_type}"
        delivery_key = hashlib.sha256(material.encode("utf-8")).hexdigest()
        created_at = self._now()
        with self._connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO public_reply_deliveries("
                "delivery_key,body,created_at) VALUES (?,?,?)",
                (delivery_key, body, created_at),
            )
            row = connection.execute(
                "SELECT body,created_at,delivered_at "
                "FROM public_reply_deliveries WHERE delivery_key = ?",
                (delivery_key,),
            ).fetchone()
        self.path.chmod(0o600)
        if row is None:
            raise PublicReplyDeliveryStateError(
                "public reply delivery attempt was not persisted"
            )
        return PublicReplyDeliveryAttempt(
            delivery_key=delivery_key,
            body=str(row["body"]),
            created_at=float(row["created_at"]),
            delivered=row["delivered_at"] is not None,
        )

    def claim(self, delivery_key: str) -> str | None:
        now = self._now()
        claim_token = secrets.token_hex(16)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                "UPDATE public_reply_deliveries "
                "SET claim_token = ?, lease_until = ? "
                "WHERE delivery_key = ? AND delivered_at IS NULL "
                "AND (claim_token = '' OR lease_until <= ?)",
                (
                    claim_token,
                    now + self.claim_lease_seconds,
                    delivery_key,
                    now,
                ),
            )
        self.path.chmod(0o600)
        return claim_token if cursor.rowcount == 1 else None

    def release_claim(self, delivery_key: str, claim_token: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE public_reply_deliveries "
                "SET claim_token = '', lease_until = 0 "
                "WHERE delivery_key = ? AND delivered_at IS NULL "
                "AND claim_token = ?",
                (delivery_key, claim_token),
            )
        self.path.chmod(0o600)

    def discard_definitely_unsent(
        self,
        delivery_key: str,
        claim_token: str,
    ) -> bool:
        """Remove an undelivered attempt after a definite pre-send abort.

        This must only be used when the transport proves that Send was never
        clicked. Ambiguous failures keep the first body for exactly-once
        reconciliation.
        """
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM public_reply_deliveries "
                "WHERE delivery_key = ? AND delivered_at IS NULL "
                "AND claim_token = ?",
                (delivery_key, claim_token),
            )
        self.path.chmod(0o600)
        return cursor.rowcount == 1

    def mark_delivered(self, delivery_key: str, claim_token: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE public_reply_deliveries "
                "SET delivered_at = ?, claim_token = '', lease_until = 0 "
                "WHERE delivery_key = ? AND delivered_at IS NULL "
                "AND claim_token = ?",
                (self._now(), delivery_key, claim_token),
            )
        self.path.chmod(0o600)
        return cursor.rowcount == 1
