from __future__ import annotations

import hashlib
import secrets
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path


def _delivery_key_v1(
    delivery_attempt_id: str,
    ticket_id: int,
    reply_type: str,
) -> str:
    material = f"v1\0{delivery_attempt_id}\0{ticket_id}\0{reply_type}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _delivery_key_v2(delivery_attempt_id: str, ticket_id: int) -> str:
    material = f"v2\0{delivery_attempt_id}\0{ticket_id}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class PublicReplyDeliveryAttempt:
    delivery_key: str
    reply_type: str
    body: str
    internal_analysis: str
    snapshot_customer_message_count: int | None
    snapshot_customer_last_message_id: str
    migrated_from_v1: bool
    created_at: float
    delivered: bool
    ambiguous: bool


class PublicReplyDeliveryStateError(RuntimeError):
    pass


class PublicReplyDeliveryStore:
    _LEGACY_REPLY_TYPES = ("ask", "solution", "handoff", "closed")

    def __init__(
        self,
        path: Path | str,
        *,
        claim_lease_seconds: float = 300.0,
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
                "reply_type TEXT NOT NULL DEFAULT '',"
                "body TEXT NOT NULL,"
                "internal_analysis TEXT NOT NULL DEFAULT '',"
                "snapshot_customer_message_count INTEGER,"
                "snapshot_customer_last_message_id TEXT NOT NULL DEFAULT '',"
                "migrated_from_v1 INTEGER NOT NULL DEFAULT 0,"
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
            if "internal_analysis" not in columns:
                connection.execute(
                    "ALTER TABLE public_reply_deliveries "
                    "ADD COLUMN internal_analysis TEXT NOT NULL DEFAULT ''"
                )
            if "reply_type" not in columns:
                connection.execute(
                    "ALTER TABLE public_reply_deliveries "
                    "ADD COLUMN reply_type TEXT NOT NULL DEFAULT ''"
                )
            if "snapshot_customer_message_count" not in columns:
                connection.execute(
                    "ALTER TABLE public_reply_deliveries "
                    "ADD COLUMN snapshot_customer_message_count INTEGER"
                )
            if "snapshot_customer_last_message_id" not in columns:
                connection.execute(
                    "ALTER TABLE public_reply_deliveries "
                    "ADD COLUMN snapshot_customer_last_message_id "
                    "TEXT NOT NULL DEFAULT ''"
                )
            if "migrated_from_v1" not in columns:
                connection.execute(
                    "ALTER TABLE public_reply_deliveries "
                    "ADD COLUMN migrated_from_v1 INTEGER NOT NULL DEFAULT 0"
                )
            if "ambiguous_at" not in columns:
                connection.execute(
                    "ALTER TABLE public_reply_deliveries "
                    "ADD COLUMN ambiguous_at REAL"
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
        internal_analysis: str = "",
        snapshot_customer_message_count: int | None = None,
        snapshot_customer_last_message_id: str | None = None,
    ) -> PublicReplyDeliveryAttempt:
        # v2 deliberately excludes generated reply_type.  A process restart may
        # classify the same logical attempt differently; that must reconcile the
        # first delivery, never create a second customer-visible side effect.
        delivery_key = _delivery_key_v2(delivery_attempt_id, ticket_id)
        created_at = self._now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT delivery_key,reply_type,body,internal_analysis,"
                "snapshot_customer_message_count,"
                "snapshot_customer_last_message_id,"
                "migrated_from_v1,"
                "created_at,delivered_at,ambiguous_at,claim_token,lease_until "
                "FROM public_reply_deliveries WHERE delivery_key = ?",
                (delivery_key,),
            ).fetchone()
            legacy_keys = {
                _delivery_key_v1(
                    delivery_attempt_id,
                    ticket_id,
                    legacy_reply_type,
                ): legacy_reply_type
                for legacy_reply_type in self._LEGACY_REPLY_TYPES
            }
            placeholders = ",".join("?" for _ in legacy_keys)
            if row is not None and row["delivered_at"] is None:
                mixed_legacy = connection.execute(
                    "SELECT 1 FROM public_reply_deliveries "
                    f"WHERE delivery_key IN ({placeholders}) LIMIT 1",
                    tuple(legacy_keys),
                ).fetchone()
                if mixed_legacy is not None:
                    raise PublicReplyDeliveryStateError(
                        "mixed v1/v2 public reply delivery rows require "
                        "manual reconciliation"
                    )
            if row is None:
                legacy_rows = connection.execute(
                    "SELECT delivery_key,reply_type,body,internal_analysis,"
                    "snapshot_customer_message_count,"
                    "snapshot_customer_last_message_id,"
                    "migrated_from_v1,"
                    "created_at,delivered_at,ambiguous_at,claim_token,lease_until "
                    "FROM public_reply_deliveries "
                    f"WHERE delivery_key IN ({placeholders})",
                    tuple(legacy_keys),
                ).fetchall()
                if len(legacy_rows) > 1:
                    raise PublicReplyDeliveryStateError(
                        "multiple legacy public reply delivery rows require "
                        "manual reconciliation"
                    )
                active_legacy = next(
                    (
                        item
                        for item in legacy_rows
                        if item["delivered_at"] is None
                        and str(item["claim_token"] or "")
                        and float(item["lease_until"] or 0) > created_at
                    ),
                    None,
                )
                if active_legacy is not None:
                    raise PublicReplyDeliveryStateError(
                        "legacy public reply delivery attempt is already in progress"
                    )
                if legacy_rows:
                    selected = min(
                        legacy_rows,
                        key=lambda item: (
                            item["delivered_at"] is None,
                            float(item["created_at"]),
                        ),
                    )
                    selected_key = str(selected["delivery_key"])
                    frozen_reply_type = (
                        str(selected["reply_type"] or "")
                        or legacy_keys[selected_key]
                    )
                    connection.execute(
                        "UPDATE public_reply_deliveries "
                        "SET delivery_key = ?, reply_type = ?, "
                        "migrated_from_v1 = 1 "
                        "WHERE delivery_key = ?",
                        (delivery_key, frozen_reply_type, selected_key),
                    )
                else:
                    connection.execute(
                        "INSERT INTO public_reply_deliveries("
                        "delivery_key,reply_type,body,internal_analysis,"
                        "snapshot_customer_message_count,"
                        "snapshot_customer_last_message_id,created_at) "
                        "VALUES (?,?,?,?,?,?,?)",
                        (
                            delivery_key,
                            reply_type,
                            body,
                            internal_analysis,
                            snapshot_customer_message_count,
                            snapshot_customer_last_message_id or "",
                            created_at,
                        ),
                    )
            # Older callers persisted only the public body.  Attach the first
            # non-empty analysis when the retry reaches the post-delivery
            # bookkeeping step, while never replacing an analysis that already
            # belongs to the logical delivery attempt.
            if internal_analysis:
                connection.execute(
                    "UPDATE public_reply_deliveries "
                    "SET internal_analysis = ? "
                    "WHERE delivery_key = ? AND internal_analysis = ''",
                    (internal_analysis, delivery_key),
                )
            connection.execute(
                "UPDATE public_reply_deliveries SET reply_type = ? "
                "WHERE delivery_key = ? AND reply_type = ''",
                (reply_type, delivery_key),
            )
            row = connection.execute(
                "SELECT reply_type,body,internal_analysis,"
                "snapshot_customer_message_count,"
                "snapshot_customer_last_message_id,migrated_from_v1,"
                "created_at,delivered_at,ambiguous_at "
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
            reply_type=str(row["reply_type"]),
            body=str(row["body"]),
            internal_analysis=str(row["internal_analysis"]),
            snapshot_customer_message_count=(
                int(row["snapshot_customer_message_count"])
                if row["snapshot_customer_message_count"] is not None
                else None
            ),
            snapshot_customer_last_message_id=str(
                row["snapshot_customer_last_message_id"] or ""
            ),
            migrated_from_v1=bool(row["migrated_from_v1"]),
            created_at=float(row["created_at"]),
            delivered=row["delivered_at"] is not None,
            ambiguous=row["ambiguous_at"] is not None,
        )

    def get(
        self,
        *,
        delivery_attempt_id: str,
        ticket_id: int,
    ) -> PublicReplyDeliveryAttempt | None:
        delivery_key = _delivery_key_v2(delivery_attempt_id, ticket_id)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT reply_type,body,internal_analysis,"
                "snapshot_customer_message_count,"
                "snapshot_customer_last_message_id,migrated_from_v1,"
                "created_at,delivered_at,ambiguous_at "
                "FROM public_reply_deliveries WHERE delivery_key = ?",
                (delivery_key,),
            ).fetchone()
        if row is None:
            return None
        return PublicReplyDeliveryAttempt(
            delivery_key=delivery_key,
            reply_type=str(row["reply_type"]),
            body=str(row["body"]),
            internal_analysis=str(row["internal_analysis"]),
            snapshot_customer_message_count=(
                int(row["snapshot_customer_message_count"])
                if row["snapshot_customer_message_count"] is not None
                else None
            ),
            snapshot_customer_last_message_id=str(
                row["snapshot_customer_last_message_id"] or ""
            ),
            migrated_from_v1=bool(row["migrated_from_v1"]),
            created_at=float(row["created_at"]),
            delivered=row["delivered_at"] is not None,
            ambiguous=row["ambiguous_at"] is not None,
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
                "AND ambiguous_at IS NULL "
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

    def renew_claim(self, delivery_key: str, claim_token: str) -> bool:
        now = self._now()
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE public_reply_deliveries SET lease_until = ? "
                "WHERE delivery_key = ? AND delivered_at IS NULL "
                "AND claim_token = ?",
                (
                    now + self.claim_lease_seconds,
                    delivery_key,
                    claim_token,
                ),
            )
        self.path.chmod(0o600)
        return cursor.rowcount == 1

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

    def mark_ambiguous(self, delivery_key: str, claim_token: str) -> bool:
        """Persist a possible remote send and fence it from automatic retry."""
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE public_reply_deliveries "
                "SET ambiguous_at = ?, claim_token = '', lease_until = 0 "
                "WHERE delivery_key = ? AND delivered_at IS NULL "
                "AND claim_token = ?",
                (self._now(), delivery_key, claim_token),
            )
        self.path.chmod(0o600)
        return cursor.rowcount == 1

    def begin_send(self, delivery_key: str, claim_token: str) -> bool:
        """Fence an attempt before the relay can possibly click Send."""
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE public_reply_deliveries SET ambiguous_at = ? "
                "WHERE delivery_key = ? AND delivered_at IS NULL "
                "AND claim_token = ?",
                (self._now(), delivery_key, claim_token),
            )
        self.path.chmod(0o600)
        return cursor.rowcount == 1

    def confirm_ambiguous_delivered(self, delivery_key: str) -> bool:
        """Close an ambiguous attempt only after deterministic remote evidence."""
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE public_reply_deliveries "
                "SET delivered_at = ?, ambiguous_at = NULL, claim_token = '', "
                "lease_until = 0 WHERE delivery_key = ? "
                "AND delivered_at IS NULL AND ambiguous_at IS NOT NULL",
                (self._now(), delivery_key),
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
