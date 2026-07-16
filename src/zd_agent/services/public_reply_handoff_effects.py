"""Crash-safe operator side effects after a delivered handoff reply."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import secrets
import sqlite3
import time

from zd_agent.log import get_logger
from zd_agent.services.public_reply_delivery_store import PublicReplyDeliveryStore

logger = get_logger(module="services.public_reply_handoff_effects")
EffectSender = Callable[..., Awaitable[bool]]


def _handoff_reference(ticket_id: int, delivery_attempt_id: str) -> str:
    digest = sha256(
        f"v1\0{ticket_id}\0{delivery_attempt_id}".encode("utf-8")
    ).hexdigest()[:16]
    return f"public-handoff:{digest}"


@dataclass(frozen=True, slots=True)
class PendingPostPublicHandoff:
    reference: str
    ticket_id: int
    delivery_attempt_id: str
    body_with_footer: str
    reason: str
    source: str
    customer_phrase: str
    public_delivery_confirmed: bool
    feishu_sent: bool
    feishu_ambiguous: bool
    zendesk_tag_sent: bool

    @property
    def complete(self) -> bool:
        return self.feishu_sent and self.zendesk_tag_sent


class PostPublicHandoffStore:
    def __init__(
        self,
        path: Path | str,
        *,
        claim_lease_seconds: float = 120.0,
    ) -> None:
        self.path = Path(path)
        self.claim_lease_seconds = max(1.0, claim_lease_seconds)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path.parent.chmod(0o700)
        with self._connect() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS post_public_handoff_effects ("
                "reference TEXT PRIMARY KEY,ticket_id INTEGER NOT NULL,"
                "delivery_attempt_id TEXT NOT NULL,body_with_footer TEXT NOT NULL,"
                "reason TEXT NOT NULL,source TEXT NOT NULL,"
                "customer_phrase TEXT NOT NULL DEFAULT '',"
                "public_delivery_confirmed INTEGER NOT NULL DEFAULT 0,"
                "feishu_sent INTEGER NOT NULL DEFAULT 0,"
                "feishu_ambiguous INTEGER NOT NULL DEFAULT 0,"
                "zendesk_tag_sent INTEGER NOT NULL DEFAULT 0,"
                "created_at REAL NOT NULL,claim_token TEXT NOT NULL DEFAULT '',"
                "lease_until REAL NOT NULL DEFAULT 0)"
            )
            columns = {
                str(row["name"])
                for row in connection.execute(
                    "PRAGMA table_info(post_public_handoff_effects)"
                )
            }
            if "feishu_ambiguous" not in columns:
                connection.execute(
                    "ALTER TABLE post_public_handoff_effects "
                    "ADD COLUMN feishu_ambiguous INTEGER NOT NULL DEFAULT 0"
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
        body_with_footer: str,
        public_delivery_confirmed: bool,
        reason: str = "agent_handoff",
        source: str = "agent_public_reply",
        customer_phrase: str = "",
    ) -> PendingPostPublicHandoff:
        reference = _handoff_reference(ticket_id, delivery_attempt_id)
        with self._connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO post_public_handoff_effects("
                "reference,ticket_id,delivery_attempt_id,body_with_footer,"
                "reason,source,customer_phrase,public_delivery_confirmed,"
                "created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    reference,
                    ticket_id,
                    delivery_attempt_id,
                    body_with_footer,
                    reason,
                    source,
                    customer_phrase,
                    int(public_delivery_confirmed),
                    time.time(),
                ),
            )
            connection.execute(
                "UPDATE post_public_handoff_effects SET body_with_footer = ?, "
                "reason = ?, source = ?, customer_phrase = ? "
                "WHERE reference = ? AND public_delivery_confirmed = 0 "
                "AND feishu_sent = 0 AND zendesk_tag_sent = 0",
                (body_with_footer, reason, source, customer_phrase, reference),
            )
            if public_delivery_confirmed:
                connection.execute(
                    "UPDATE post_public_handoff_effects "
                    "SET public_delivery_confirmed = 1 WHERE reference = ?",
                    (reference,),
                )
            row = connection.execute(
                "SELECT * FROM post_public_handoff_effects WHERE reference = ?",
                (reference,),
            ).fetchone()
        if row is None:
            raise RuntimeError("post-public handoff effects were not persisted")
        return _from_row(row)

    def pending(self, *, limit: int = 100) -> list[PendingPostPublicHandoff]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM post_public_handoff_effects "
                "WHERE feishu_sent = 0 OR zendesk_tag_sent = 0 "
                "ORDER BY created_at LIMIT ?",
                (max(1, limit),),
            ).fetchall()
        return [_from_row(row) for row in rows]

    def claim(self, reference: str) -> str | None:
        now = time.time()
        token = secrets.token_hex(16)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                "UPDATE post_public_handoff_effects "
                "SET claim_token = ?, lease_until = ? WHERE reference = ? "
                "AND (feishu_sent = 0 OR zendesk_tag_sent = 0) "
                "AND (claim_token = '' OR lease_until <= ?)",
                (token, now + self.claim_lease_seconds, reference, now),
            )
        return token if cursor.rowcount == 1 else None

    def renew(self, reference: str, token: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE post_public_handoff_effects SET lease_until = ? "
                "WHERE reference = ? AND claim_token = ?",
                (time.time() + self.claim_lease_seconds, reference, token),
            )
        return cursor.rowcount == 1

    def release(self, reference: str, token: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE post_public_handoff_effects "
                "SET claim_token = '', lease_until = 0 "
                "WHERE reference = ? AND claim_token = ?",
                (reference, token),
            )

    def mark_effect(self, reference: str, token: str, column: str) -> bool:
        if column not in {"feishu_sent", "zendesk_tag_sent"}:
            raise ValueError(f"unsupported handoff effect: {column}")
        with self._connect() as connection:
            cursor = connection.execute(
                f"UPDATE post_public_handoff_effects SET {column} = 1, "
                "feishu_ambiguous = CASE WHEN ? = 'feishu_sent' THEN 0 "
                "ELSE feishu_ambiguous END "
                "WHERE reference = ? AND claim_token = ?",
                (column, reference, token),
            )
        return cursor.rowcount == 1

    def mark_feishu_ambiguous(self, reference: str, token: str) -> bool:
        """Fence a timed-out remote notification from automatic re-delivery."""
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE post_public_handoff_effects SET feishu_ambiguous = 1 "
                "WHERE reference = ? AND claim_token = ?",
                (reference, token),
            )
        return cursor.rowcount == 1

    def begin_feishu_send(self, reference: str, token: str) -> bool:
        """Persist the no-resend fence before invoking Feishu."""
        return self.mark_feishu_ambiguous(reference, token)

    def clear_feishu_ambiguous_if_definitely_unsent(
        self, reference: str, token: str,
    ) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE post_public_handoff_effects SET feishu_ambiguous = 0 "
                "WHERE reference = ? AND claim_token = ? AND feishu_sent = 0",
                (reference, token),
            )
        return cursor.rowcount == 1

    def confirm_public_delivery(self, reference: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE post_public_handoff_effects "
                "SET public_delivery_confirmed = 1 WHERE reference = ?",
                (reference,),
            )
        return cursor.rowcount == 1


def _from_row(row: sqlite3.Row) -> PendingPostPublicHandoff:
    return PendingPostPublicHandoff(
        reference=str(row["reference"]),
        ticket_id=int(row["ticket_id"]),
        delivery_attempt_id=str(row["delivery_attempt_id"]),
        body_with_footer=str(row["body_with_footer"]),
        reason=str(row["reason"]),
        source=str(row["source"]),
        customer_phrase=str(row["customer_phrase"]),
        public_delivery_confirmed=bool(row["public_delivery_confirmed"]),
        feishu_sent=bool(row["feishu_sent"]),
        feishu_ambiguous=bool(row["feishu_ambiguous"]),
        zendesk_tag_sent=bool(row["zendesk_tag_sent"]),
    )


async def _renew_claim(
    store: PostPublicHandoffStore,
    reference: str,
    token: str,
) -> None:
    interval = max(0.1, min(30.0, store.claim_lease_seconds / 3))
    while True:
        await asyncio.sleep(interval)
        if not store.renew(reference, token):
            return


async def ensure_post_public_handoff_effects(
    *,
    ticket_id: int,
    delivery_attempt_id: str,
    body_with_footer: str,
    store: PostPublicHandoffStore,
    public_delivery_confirmed: bool,
    reason: str = "agent_handoff",
    source: str = "agent_public_reply",
    customer_phrase: str = "",
    feishu_sender: EffectSender | None = None,
    tag_sender: EffectSender | None = None,
) -> bool:
    pending = store.prepare(
        ticket_id=ticket_id,
        delivery_attempt_id=delivery_attempt_id,
        body_with_footer=body_with_footer,
        public_delivery_confirmed=public_delivery_confirmed,
        reason=reason,
        source=source,
        customer_phrase=customer_phrase,
    )
    if not pending.public_delivery_confirmed:
        return False
    if pending.complete:
        return True
    token = store.claim(pending.reference)
    if token is None:
        return False
    if feishu_sender is None or tag_sender is None:
        from zd_agent.services import escalation_notifications

        feishu_sender = (
            feishu_sender
            or escalation_notifications.send_feishu_escalation_best_effort
        )
        tag_sender = (
            tag_sender
            or escalation_notifications.add_zendesk_escalation_tag_best_effort
        )

    heartbeat = asyncio.create_task(
        _renew_claim(store, pending.reference, token)
    )
    feishu_sent = pending.feishu_sent
    feishu_definitely_unsent = False
    tag_sent = pending.zendesk_tag_sent
    try:
        if not feishu_sent and pending.feishu_ambiguous:
            logger.warning(
                "post_public_handoff_feishu_ambiguous_pending_manual_reconcile",
                ticket_id=ticket_id,
                reference=pending.reference,
            )
        elif not feishu_sent:
            try:
                if not store.begin_feishu_send(pending.reference, token):
                    return False
                feishu_sent = await feishu_sender(
                    ticket_id=ticket_id,
                    reason=pending.reason,
                    source=pending.source,
                    customer_phrase=pending.customer_phrase,
                    agent_last_reply=pending.body_with_footer,
                    handoff_reply_sent=True,
                    idempotency_reference=pending.reference,
                )
                feishu_definitely_unsent = feishu_sent is False
            except Exception as exc:  # noqa: BLE001 - tag remains independent.
                logger.warning(
                    "post_public_handoff_feishu_failed",
                    ticket_id=ticket_id,
                    error=str(exc)[:200],
                )
            if feishu_sent:
                if not store.mark_effect(pending.reference, token, "feishu_sent"):
                    logger.warning(
                        "post_public_handoff_feishu_confirmation_failed",
                        ticket_id=ticket_id,
                        reference=pending.reference,
                    )
                    feishu_sent = False
            elif feishu_definitely_unsent:
                # Sender's normal False result is its explicit
                # definite-unsent contract; only that permits a later retry.
                store.clear_feishu_ambiguous_if_definitely_unsent(
                    pending.reference, token,
                )
        if not tag_sent:
            try:
                tag_sent = await tag_sender(ticket_id=ticket_id)
            except Exception as exc:  # noqa: BLE001 - retry from durable row.
                logger.warning(
                    "post_public_handoff_tag_failed",
                    ticket_id=ticket_id,
                    error=str(exc)[:200],
                )
            if tag_sent:
                store.mark_effect(pending.reference, token, "zendesk_tag_sent")
        return bool(feishu_sent and tag_sent)
    finally:
        heartbeat.cancel()
        with suppress(asyncio.CancelledError):
            await heartbeat
        store.release(pending.reference, token)


async def reconcile_pending_post_public_handoff_effects(
    *,
    store: PostPublicHandoffStore,
    delivery_store: PublicReplyDeliveryStore,
    feishu_sender: EffectSender | None = None,
    tag_sender: EffectSender | None = None,
    limit: int = 100,
) -> int:
    reconciled = 0
    for pending in store.pending(limit=limit):
        if not pending.public_delivery_confirmed:
            delivery = delivery_store.get(
                delivery_attempt_id=pending.delivery_attempt_id,
                ticket_id=pending.ticket_id,
            )
            if (
                delivery is None
                or not delivery.delivered
                or delivery.reply_type != "handoff"
            ):
                continue
            store.confirm_public_delivery(pending.reference)
        if await ensure_post_public_handoff_effects(
            ticket_id=pending.ticket_id,
            delivery_attempt_id=pending.delivery_attempt_id,
            body_with_footer=pending.body_with_footer,
            store=store,
            public_delivery_confirmed=True,
            reason=pending.reason,
            source=pending.source,
            customer_phrase=pending.customer_phrase,
            feishu_sender=feishu_sender,
            tag_sender=tag_sender,
        ):
            reconciled += 1
    return reconciled
