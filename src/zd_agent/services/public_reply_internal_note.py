"""Create one idempotent internal analysis note for each public reply."""

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
from typing import Any

from zd_agent.log import get_logger
from zd_agent.services.public_reply_delivery_store import PublicReplyDeliveryStore

logger = get_logger(module="services.public_reply_internal_note")

ZendeskToolCaller = Callable[..., Awaitable[dict[str, Any]]]


@dataclass(frozen=True, slots=True)
class PendingPostPublicInternalNote:
    reference: str
    ticket_id: int
    delivery_attempt_id: str
    reply_type: str
    body_with_footer: str
    internal_analysis: str
    public_delivery_confirmed: bool
    accept_legacy_reference: bool
    write_attempted: bool


class PostPublicInternalNoteStore:
    """Durable outbox for the mandatory note following a public reply."""

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
                "CREATE TABLE IF NOT EXISTS post_public_internal_notes ("
                "reference TEXT PRIMARY KEY,"
                "ticket_id INTEGER NOT NULL,"
                "delivery_attempt_id TEXT NOT NULL DEFAULT '',"
                "reply_type TEXT NOT NULL,"
                "body_with_footer TEXT NOT NULL,"
                "internal_analysis TEXT NOT NULL DEFAULT '',"
                "public_delivery_confirmed INTEGER NOT NULL DEFAULT 0,"
                "accept_legacy_reference INTEGER NOT NULL DEFAULT 0,"
                "write_attempted INTEGER NOT NULL DEFAULT 0,"
                "created_at REAL NOT NULL DEFAULT 0,"
                "claim_token TEXT NOT NULL DEFAULT '',"
                "lease_until REAL NOT NULL DEFAULT 0)"
            )
            columns = {
                str(row["name"])
                for row in connection.execute(
                    "PRAGMA table_info(post_public_internal_notes)"
                )
            }
            legacy_rows_precede_delivery_confirmation = (
                "public_delivery_confirmed" not in columns
            )
            migrations = {
                "internal_analysis": (
                    "TEXT NOT NULL DEFAULT ''"
                ),
                "delivery_attempt_id": "TEXT NOT NULL DEFAULT ''",
                "public_delivery_confirmed": "INTEGER NOT NULL DEFAULT 0",
                "accept_legacy_reference": "INTEGER NOT NULL DEFAULT 0",
                "write_attempted": "INTEGER NOT NULL DEFAULT 0",
                "created_at": "REAL NOT NULL DEFAULT 0",
                "claim_token": "TEXT NOT NULL DEFAULT ''",
                "lease_until": "REAL NOT NULL DEFAULT 0",
            }
            for column, definition in migrations.items():
                if column not in columns:
                    connection.execute(
                        "ALTER TABLE post_public_internal_notes "
                        f"ADD COLUMN {column} {definition}"
                    )
            if legacy_rows_precede_delivery_confirmation:
                # Before this column existed, rows were created only after a
                # public reply had already returned successfully.
                connection.execute(
                    "UPDATE post_public_internal_notes "
                    "SET public_delivery_confirmed = 1"
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
        delivery_attempt_id: str = "",
        reply_type: str,
        body_with_footer: str,
        internal_analysis: str,
        public_delivery_confirmed: bool = True,
        accept_legacy_reference: bool = False,
    ) -> PendingPostPublicInternalNote:
        reference = _analysis_reference(
            ticket_id=ticket_id,
            body_with_footer=body_with_footer,
            delivery_attempt_id=delivery_attempt_id,
        )
        with self._connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO post_public_internal_notes("
                "reference,ticket_id,delivery_attempt_id,reply_type,"
                "body_with_footer,internal_analysis,"
                "public_delivery_confirmed,accept_legacy_reference,created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    reference,
                    ticket_id,
                    delivery_attempt_id,
                    reply_type,
                    body_with_footer,
                    internal_analysis,
                    int(public_delivery_confirmed),
                    int(accept_legacy_reference),
                    time.time(),
                ),
            )
            if internal_analysis:
                connection.execute(
                    "UPDATE post_public_internal_notes "
                    "SET internal_analysis = ? "
                    "WHERE reference = ? AND internal_analysis = ''",
                    (internal_analysis, reference),
                )
            if public_delivery_confirmed:
                connection.execute(
                    "UPDATE post_public_internal_notes "
                    "SET public_delivery_confirmed = 1 WHERE reference = ?",
                    (reference,),
                )
            if accept_legacy_reference:
                connection.execute(
                    "UPDATE post_public_internal_notes "
                    "SET accept_legacy_reference = 1 WHERE reference = ?",
                    (reference,),
                )
            row = connection.execute(
                "SELECT reference,ticket_id,delivery_attempt_id,reply_type,"
                "body_with_footer,internal_analysis,"
                "public_delivery_confirmed,accept_legacy_reference "
                ",write_attempted "
                "FROM post_public_internal_notes "
                "WHERE reference = ?",
                (reference,),
            ).fetchone()
        self.path.chmod(0o600)
        if row is None:
            raise RuntimeError("post-public internal note was not persisted")
        return _pending_note_from_row(row)

    def pending(self, *, limit: int = 100) -> list[PendingPostPublicInternalNote]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT reference,ticket_id,delivery_attempt_id,reply_type,"
                "body_with_footer,internal_analysis,"
                "public_delivery_confirmed,accept_legacy_reference "
                ",write_attempted "
                "FROM post_public_internal_notes "
                "ORDER BY created_at ASC LIMIT ?",
                (max(1, limit),),
            ).fetchall()
        return [_pending_note_from_row(row) for row in rows]

    def claim(self, reference: str) -> str | None:
        now = time.time()
        claim_token = secrets.token_hex(16)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                "UPDATE post_public_internal_notes "
                "SET claim_token = ?, lease_until = ? "
                "WHERE reference = ? "
                "AND (claim_token = '' OR lease_until <= ?)",
                (
                    claim_token,
                    now + self.claim_lease_seconds,
                    reference,
                    now,
                ),
            )
        return claim_token if cursor.rowcount == 1 else None

    def release(self, reference: str, claim_token: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE post_public_internal_notes "
                "SET claim_token = '', lease_until = 0 "
                "WHERE reference = ? AND claim_token = ?",
                (reference, claim_token),
            )

    def renew(self, reference: str, claim_token: str) -> bool:
        now = time.time()
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE post_public_internal_notes SET lease_until = ? "
                "WHERE reference = ? AND claim_token = ?",
                (now + self.claim_lease_seconds, reference, claim_token),
            )
        return cursor.rowcount == 1

    def confirm_public_delivery(self, reference: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE post_public_internal_notes "
                "SET public_delivery_confirmed = 1 WHERE reference = ?",
                (reference,),
            )
        return cursor.rowcount == 1

    def mark_write_attempted(self, reference: str, claim_token: str) -> bool:
        """Durably fence an ambiguous remote write before issuing it."""
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE post_public_internal_notes SET write_attempted = 1 "
                "WHERE reference = ? AND claim_token = ?",
                (reference, claim_token),
            )
        return cursor.rowcount == 1

    def clear_write_attempted_if_definitely_unsent(
        self, reference: str, claim_token: str,
    ) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE post_public_internal_notes SET write_attempted = 0 "
                "WHERE reference = ? AND claim_token = ?",
                (reference, claim_token),
            )
        return cursor.rowcount == 1

    def mark_delivered(self, reference: str, claim_token: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM post_public_internal_notes "
                "WHERE reference = ? AND claim_token = ?",
                (reference, claim_token),
            )
        return cursor.rowcount == 1


def _pending_note_from_row(row: sqlite3.Row) -> PendingPostPublicInternalNote:
    return PendingPostPublicInternalNote(
        reference=str(row["reference"]),
        ticket_id=int(row["ticket_id"]),
        delivery_attempt_id=str(row["delivery_attempt_id"]),
        reply_type=str(row["reply_type"]),
        body_with_footer=str(row["body_with_footer"]),
        internal_analysis=str(row["internal_analysis"]),
        public_delivery_confirmed=bool(row["public_delivery_confirmed"]),
        accept_legacy_reference=bool(row["accept_legacy_reference"]),
        write_attempted=bool(row["write_attempted"]),
    )


async def _renew_note_claim(
    store: PostPublicInternalNoteStore,
    reference: str,
    claim_token: str,
) -> None:
    interval = max(0.1, min(30.0, store.claim_lease_seconds / 3))
    while True:
        await asyncio.sleep(interval)
        if not store.renew(reference, claim_token):
            return

_DEFAULT_ANALYSIS = {
    "ask": (
        "The available information was insufficient for a reliable diagnosis. "
        "The public reply requested the minimum missing details needed to continue."
    ),
    "solution": (
        "The agent completed the available evidence checks and sent the customer a "
        "technical response. See the run archive for the full evidence trail."
    ),
    "handoff": (
        "The issue requires human ownership or action. The customer was told that a "
        "support engineer will follow up."
    ),
    "closed": (
        "The customer reported that the issue was resolved. The public reply "
        "acknowledged closure and explained how to reopen the conversation."
    ),
}


def _analysis_reference(
    *,
    ticket_id: int,
    body_with_footer: str,
    delivery_attempt_id: str = "",
) -> str:
    normalized = " ".join((body_with_footer or "").split())
    if delivery_attempt_id:
        material = f"v2\0{ticket_id}\0{delivery_attempt_id}\0{normalized}"
    else:
        # Preserve the exact production v1 hash so recovery can recognize
        # internal notes written before delivery-attempt references existed.
        material = f"{ticket_id}\0{normalized}"
    digest = sha256(material.encode()).hexdigest()[:16]
    return f"public-reply-analysis:{digest}"


def build_post_public_internal_note(
    *,
    ticket_id: int,
    reply_type: str,
    body_with_footer: str,
    internal_analysis: str = "",
    delivery_attempt_id: str = "",
) -> str:
    """Build the concise internal record written after customer delivery."""
    reference = _analysis_reference(
        ticket_id=ticket_id,
        body_with_footer=body_with_footer,
        delivery_attempt_id=delivery_attempt_id,
    )
    analysis = (internal_analysis or "").strip() or _DEFAULT_ANALYSIS.get(
        reply_type,
        _DEFAULT_ANALYSIS["solution"],
    )
    return (
        "🤖 **AI Agent public reply record**\n\n"
        "## Internal Analysis\n\n"
        f"{analysis}\n\n"
        "## Delivery\n\n"
        "- Result: Public reply delivered to the customer\n"
        f"- Reply type: {reply_type}\n"
        "- Channel: Zendesk Messaging\n"
        f"- Reference: `{reference}`"
    )


def _comments_from_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = payload.get("data")
    if not isinstance(data, dict):
        return []
    comments = data.get("comments")
    if not isinstance(comments, list):
        return []
    return [item for item in comments if isinstance(item, dict)]


def _payload_contains_reference(payload: dict[str, Any], reference: str) -> bool:
    for comment in _comments_from_payload(payload):
        for key in ("plain_body", "body", "html_body"):
            value = comment.get(key)
            if isinstance(value, str) and reference in value:
                return True
    return False


def _definitely_unsent_note_result(result: dict[str, Any]) -> bool:
    """Only explicit validation/auth rejections prove Zendesk did not write."""
    code = str(result.get("code") or "").upper()
    return code in {
        "VALIDATION_ERROR", "INVALID_ARGUMENT", "BAD_REQUEST", "UNAUTHORIZED",
        "FORBIDDEN", "NOT_FOUND", "DRY_RUN", "ZENDESK_MCP_REQUEST_FAILED",
    }


async def ensure_post_public_internal_note(
    *,
    ticket_id: int,
    reply_type: str,
    body_with_footer: str,
    internal_analysis: str,
    zendesk_tool_caller: ZendeskToolCaller,
    note_store: PostPublicInternalNoteStore | None = None,
    delivery_attempt_id: str = "",
    public_delivery_confirmed: bool = True,
    accept_legacy_reference: bool = False,
) -> bool:
    """Write one note only after public delivery is durably confirmed."""
    claim_token: str | None = None
    heartbeat_task: asyncio.Task[None] | None = None
    write_attempted = False
    if note_store is not None:
        pending = note_store.prepare(
            ticket_id=ticket_id,
            delivery_attempt_id=delivery_attempt_id,
            reply_type=reply_type,
            body_with_footer=body_with_footer,
            internal_analysis=internal_analysis,
            public_delivery_confirmed=public_delivery_confirmed,
            accept_legacy_reference=accept_legacy_reference,
        )
        ticket_id = pending.ticket_id
        delivery_attempt_id = pending.delivery_attempt_id
        reply_type = pending.reply_type
        body_with_footer = pending.body_with_footer
        internal_analysis = pending.internal_analysis
        accept_legacy_reference = pending.accept_legacy_reference
        write_attempted = pending.write_attempted
        reference = pending.reference
        if not pending.public_delivery_confirmed:
            logger.info(
                "post_public_internal_note_waiting_for_delivery",
                ticket_id=ticket_id,
                reference=reference,
            )
            return False
        claim_token = note_store.claim(reference)
        if claim_token is None:
            logger.info(
                "post_public_internal_note_already_in_progress",
                ticket_id=ticket_id,
                reference=reference,
            )
            return False
        heartbeat_task = asyncio.create_task(
            _renew_note_claim(note_store, reference, claim_token)
        )
    else:
        reference = _analysis_reference(
            ticket_id=ticket_id,
            body_with_footer=body_with_footer,
            delivery_attempt_id=delivery_attempt_id,
        )

    def _complete_durable_note() -> bool:
        if note_store is None or claim_token is None:
            return True
        return note_store.mark_delivered(reference, claim_token)

    lookup_args = {
        "ticket_id": ticket_id,
        "include_conversation_log": False,
        "force_refresh": True,
    }
    note_body = build_post_public_internal_note(
        ticket_id=ticket_id,
        reply_type=reply_type,
        body_with_footer=body_with_footer,
        internal_analysis=internal_analysis,
        delivery_attempt_id=delivery_attempt_id,
    )
    completed = False
    try:
        try:
            existing = await zendesk_tool_caller(
                "get_ticket_comments",
                lookup_args,
            )
        except Exception as exc:  # noqa: BLE001 - leave durable work pending.
            logger.warning(
                "post_public_internal_note_lookup_exception",
                ticket_id=ticket_id,
                error=str(exc)[:200],
                exc_info=True,
            )
            return False
        if existing.get("success") is not True:
            logger.warning(
                "post_public_internal_note_lookup_unavailable",
                ticket_id=ticket_id,
                code=existing.get("code"),
                error=str(existing.get("error") or "unknown")[:200],
            )
            return False
        legacy_reference = _analysis_reference(
            ticket_id=ticket_id,
            body_with_footer=body_with_footer,
        )
        if _payload_contains_reference(existing, reference) or (
            accept_legacy_reference
            and _payload_contains_reference(existing, legacy_reference)
        ):
            completed = _complete_durable_note()
            logger.info(
                "post_public_internal_note_already_exists",
                ticket_id=ticket_id,
                reference=reference,
            )
            return completed

        # Once a remote write was started, an exception or lost response is
        # indistinguishable from a successful note.  Never issue a second
        # write: retain the durable row for a later lookup/manual recovery.
        if write_attempted:
            logger.warning(
                "post_public_internal_note_ambiguous_write_pending",
                ticket_id=ticket_id,
                reference=reference,
            )
            return False

        try:
            if note_store is not None and claim_token is not None:
                if not note_store.mark_write_attempted(reference, claim_token):
                    return False
            result = await zendesk_tool_caller(
                "add_internal_note",
                {
                    "ticket_id": ticket_id,
                    "body": note_body,
                    "explicit_user_internal_note_request": (
                        "post-public internal analysis"
                    ),
                    "dry_run": False,
                    "confirm": True,
                    "actor": "zd-agent",
                },
            )
        except Exception as exc:  # noqa: BLE001 - the remote write is ambiguous.
            result = {"success": False, "error": str(exc)}
            logger.warning(
                "post_public_internal_note_write_exception",
                ticket_id=ticket_id,
                error=str(exc)[:200],
                exc_info=True,
            )
        if result.get("success") is True:
            completed = _complete_durable_note()
            logger.info(
                "post_public_internal_note_sent",
                ticket_id=ticket_id,
                reference=reference,
            )
            return completed

        if (
            note_store is not None
            and claim_token is not None
            and _definitely_unsent_note_result(result)
        ):
            note_store.clear_write_attempted_if_definitely_unsent(
                reference, claim_token,
            )

        try:
            verified = await zendesk_tool_caller(
                "get_ticket_comments",
                lookup_args,
            )
        except Exception as exc:  # noqa: BLE001 - never repeat an ambiguous write.
            logger.warning(
                "post_public_internal_note_verify_exception",
                ticket_id=ticket_id,
                error=str(exc)[:200],
                exc_info=True,
            )
            return False
        if verified.get("success") is True and _payload_contains_reference(
            verified,
            reference,
        ):
            completed = _complete_durable_note()
            logger.info(
                "post_public_internal_note_verified_after_error",
                ticket_id=ticket_id,
                reference=reference,
            )
            return completed
        logger.warning(
            "post_public_internal_note_write_unconfirmed",
            ticket_id=ticket_id,
            code=result.get("code"),
            error=str(result.get("error") or "unknown")[:200],
        )
        return False
    finally:
        if heartbeat_task is not None:
            heartbeat_task.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat_task
        if note_store is not None and claim_token is not None and not completed:
            note_store.release(reference, claim_token)


async def reconcile_pending_post_public_internal_notes(
    *,
    note_store: PostPublicInternalNoteStore,
    zendesk_tool_caller: ZendeskToolCaller,
    delivery_store: PublicReplyDeliveryStore | None = None,
    limit: int = 100,
) -> int:
    """Retry pending notes after restart; each remote reference is idempotent."""
    reconciled = 0
    for pending in note_store.pending(limit=limit):
        if not pending.public_delivery_confirmed:
            if delivery_store is None or not pending.delivery_attempt_id:
                continue
            delivery = delivery_store.get(
                delivery_attempt_id=pending.delivery_attempt_id,
                ticket_id=pending.ticket_id,
            )
            if delivery is None or not delivery.delivered:
                continue
            note_store.confirm_public_delivery(pending.reference)
        if await ensure_post_public_internal_note(
            ticket_id=pending.ticket_id,
            delivery_attempt_id=pending.delivery_attempt_id,
            reply_type=pending.reply_type,
            body_with_footer=pending.body_with_footer,
            internal_analysis=pending.internal_analysis,
            zendesk_tool_caller=zendesk_tool_caller,
            note_store=note_store,
            public_delivery_confirmed=True,
            accept_legacy_reference=pending.accept_legacy_reference,
        ):
            reconciled += 1
    return reconciled
