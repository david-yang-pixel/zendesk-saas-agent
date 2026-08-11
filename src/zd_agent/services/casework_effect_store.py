"""Durable v2 public-reply intent and typed ownership command store."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import secrets
import sqlite3
import time

from zd_agent.casework.domain.enums import OwnershipAction, ReasonCode


CASEWORK_EFFECT_RETENTION_SECONDS = 30 * 24 * 60 * 60


_OWNERSHIP_REASON_BY_ACTION: dict[OwnershipAction, frozenset[ReasonCode]] = {
    OwnershipAction.SUPPORT: frozenset(
        {
            ReasonCode.CUSTOMER_REQUESTED_HUMAN,
            ReasonCode.SENSITIVE_ACCOUNT_MUTATION_REQUIRED,
            ReasonCode.AUTOMATION_RETRY_EXHAUSTED,
            ReasonCode.POLICY_COMMITMENT_REQUIRED,
        },
    ),
    OwnershipAction.BILLING: frozenset({ReasonCode.BUSINESS_APPROVAL_REQUIRED}),
    OwnershipAction.ENGINEERING: frozenset({ReasonCode.CONFIRMED_PLATFORM_DEFECT}),
    OwnershipAction.SRE: frozenset({ReasonCode.CONFIRMED_SERVICE_INCIDENT}),
    OwnershipAction.SECURITY: frozenset(
        {ReasonCode.SECURITY_OR_PRIVACY_REVIEW_REQUIRED},
    ),
}
_NO_OWNERSHIP_REASONS = frozenset(
    {
        ReasonCode.RESOLVED_WITH_EVIDENCE,
        ReasonCode.BOUNDED_AUTHORITATIVE_ANSWER,
        ReasonCode.CUSTOMER_INPUT_REQUIRED,
        ReasonCode.INTERNAL_RETRY_PENDING,
    },
)
_REPLY_TYPES = frozenset({"ask", "solution", "closed", "none"})


def _validate_effect_command(
    *,
    ticket_id: int,
    delivery_attempt_id: str,
    generation: int,
    source_event_id: str,
    reply_type: str,
    body: str,
    snapshot_customer_last_message_id: str,
    ownership_action: str,
    reason_code: str,
    ownership_packet: str,
    delivery_required: bool,
) -> tuple[OwnershipAction, ReasonCode]:
    if ticket_id <= 0 or generation < 0:
        raise ValueError("casework effect requires a valid ticket generation")
    if not delivery_attempt_id.strip() or not source_event_id.strip():
        raise ValueError("casework effect requires a durable event identity")
    if not snapshot_customer_last_message_id.strip():
        raise ValueError("casework effect requires a customer snapshot cursor")
    if reply_type not in _REPLY_TYPES:
        raise ValueError(f"unsupported casework reply type: {reply_type!r}")
    try:
        owner = OwnershipAction(ownership_action)
        reason = ReasonCode(reason_code)
    except ValueError as error:
        raise ValueError(
            "casework effect contains an invalid typed owner command"
        ) from error
    if owner is OwnershipAction.NONE:
        if reason not in _NO_OWNERSHIP_REASONS:
            raise ValueError("no-owner effect carries an ownership-only reason")
        if ownership_packet.strip():
            raise ValueError("no-owner effect cannot carry an ownership packet")
    else:
        if reason not in _OWNERSHIP_REASON_BY_ACTION[owner]:
            raise ValueError("casework owner and reason are inconsistent")
        if not ownership_packet.strip():
            raise ValueError("typed ownership requires a non-empty action packet")
    if reply_type == "none":
        if delivery_required or body.strip():
            raise ValueError("no-reply effect cannot require or contain delivery")
    elif not body.strip():
        raise ValueError("customer-visible effect requires a reply body")
    return owner, reason


def casework_effect_reference(ticket_id: int, delivery_attempt_id: str) -> str:
    """Return the deterministic durable reference without opening the store."""
    digest = sha256(
        f"v1\0{ticket_id}\0{delivery_attempt_id}".encode("utf-8"),
    ).hexdigest()[:20]
    return f"casework-effect:{digest}"


def _root_delivery_attempt_id(delivery_attempt_id: str) -> str:
    return delivery_attempt_id.split(":next:", 1)[0]


def _next_delivery_attempt_id(
    delivery_attempt_id: str,
    *,
    source_event_id: str,
    snapshot_customer_message_count: int | None,
    snapshot_customer_last_message_id: str,
) -> str:
    """Derive a stable logical send key for a later customer cursor."""
    cursor = sha256(
        (
            f"v2\0{delivery_attempt_id}\0{source_event_id}\0"
            f"{snapshot_customer_message_count}\0"
            f"{snapshot_customer_last_message_id}"
        ).encode("utf-8")
    ).hexdigest()[:16]
    return f"{delivery_attempt_id}:next:{cursor}"


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
    root_delivery_attempt_id: str = ""
    authority_projection_json: str = ""
    delivery_required: bool = True
    created_at: float = 0.0
    updated_at: float = 0.0

    @property
    def ownership_required(self) -> bool:
        return self.ownership_action != "none"

    @property
    def ownership_ready(self) -> bool:
        return not self.delivery_required or self.public_delivery_confirmed

    @property
    def ownership_complete(self) -> bool:
        return not self.ownership_required or (
            self.notification_sent and self.zendesk_tag_sent
        )

    @property
    def complete(self) -> bool:
        return self.ownership_ready and self.ownership_complete


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
                "root_delivery_attempt_id TEXT NOT NULL,"
                "source_event_id TEXT NOT NULL,reply_type TEXT NOT NULL,"
                "body TEXT NOT NULL,internal_analysis TEXT NOT NULL,"
                "snapshot_customer_message_count INTEGER,"
                "snapshot_customer_last_message_id TEXT NOT NULL,"
                "ownership_action TEXT NOT NULL,reason_code TEXT NOT NULL,"
                "ownership_packet TEXT NOT NULL,"
                "authority_projection_json TEXT NOT NULL DEFAULT '',"
                "delivery_required INTEGER NOT NULL DEFAULT 1,"
                "public_delivery_confirmed INTEGER NOT NULL DEFAULT 0,"
                "notification_sent INTEGER NOT NULL DEFAULT 0,"
                "notification_ambiguous INTEGER NOT NULL DEFAULT 0,"
                "zendesk_tag_sent INTEGER NOT NULL DEFAULT 0,"
                "created_at REAL NOT NULL,updated_at REAL NOT NULL,"
                "claim_token TEXT NOT NULL DEFAULT '',"
                "lease_until REAL NOT NULL DEFAULT 0)"
            )
            columns = {
                str(row[1])
                for row in connection.execute(
                    "PRAGMA table_info(casework_effects)",
                ).fetchall()
            }
            if "delivery_required" not in columns:
                connection.execute(
                    "ALTER TABLE casework_effects ADD COLUMN "
                    "delivery_required INTEGER NOT NULL DEFAULT 1",
                )
            if "root_delivery_attempt_id" not in columns:
                connection.execute(
                    "ALTER TABLE casework_effects ADD COLUMN "
                    "root_delivery_attempt_id TEXT NOT NULL DEFAULT ''",
                )
            if "authority_projection_json" not in columns:
                connection.execute(
                    "ALTER TABLE casework_effects ADD COLUMN "
                    "authority_projection_json TEXT NOT NULL DEFAULT ''",
                )
            if "updated_at" not in columns:
                connection.execute(
                    "ALTER TABLE casework_effects ADD COLUMN "
                    "updated_at REAL NOT NULL DEFAULT 0",
                )
            self._migrate_legacy_authority(connection, columns=columns)
            rows = connection.execute(
                "SELECT reference,delivery_attempt_id,root_delivery_attempt_id "
                "FROM casework_effects",
            ).fetchall()
            for row in rows:
                expected_root = _root_delivery_attempt_id(
                    str(row["delivery_attempt_id"]),
                )
                if str(row["root_delivery_attempt_id"]) != expected_root:
                    connection.execute(
                        "UPDATE casework_effects "
                        "SET root_delivery_attempt_id = ? WHERE reference = ?",
                        (expected_root, str(row["reference"])),
                    )
            connection.execute(
                "UPDATE casework_effects SET updated_at = created_at "
                "WHERE updated_at = 0",
            )
            duplicate = connection.execute(
                "SELECT ticket_id,root_delivery_attempt_id,COUNT(*) AS count "
                "FROM casework_effects WHERE delivery_required = 1 "
                "AND public_delivery_confirmed = 0 "
                "GROUP BY ticket_id,root_delivery_attempt_id HAVING COUNT(*) > 1 "
                "LIMIT 1",
            ).fetchone()
            if duplicate is not None:
                raise RuntimeError(
                    "multiple unresolved casework delivery descendants require "
                    "manual reconciliation"
                )
            connection.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS "
                "casework_effects_one_unresolved_delivery "
                "ON casework_effects(ticket_id,root_delivery_attempt_id) "
                "WHERE delivery_required = 1 "
                "AND public_delivery_confirmed = 0",
            )
            try:
                for row in connection.execute(
                    "SELECT * FROM casework_effects",
                ).fetchall():
                    _from_row(row)
            except ValueError as error:
                raise RuntimeError(
                    "invalid historical casework effect requires manual reconciliation"
                ) from error
        self.path.chmod(0o600)

    @staticmethod
    def _migrate_legacy_authority(
        connection: sqlite3.Connection,
        *,
        columns: set[str],
    ) -> None:
        if "authority_run_json" not in columns:
            return
        from zd_agent.casework.domain.effects import EffectAuthorityProjection
        from zd_agent.casework.domain.run import CaseworkRun

        rows = connection.execute(
            "SELECT reference,generation,source_event_id,"
            "snapshot_customer_message_count,snapshot_customer_last_message_id,"
            "authority_run_json,authority_projection_json "
            "FROM casework_effects WHERE authority_run_json != ''",
        ).fetchall()
        for row in rows:
            if str(row["authority_projection_json"]):
                continue
            run = CaseworkRun.model_validate_json(str(row["authority_run_json"]))
            if run.context.ticket.generation != int(
                row["generation"]
            ) or run.context.ticket.latest_event_id != str(row["source_event_id"]):
                raise RuntimeError(
                    "legacy casework authority does not match its frozen effect"
                )
            projection = EffectAuthorityProjection.from_run(
                run,
                snapshot_customer_message_count=(
                    int(row["snapshot_customer_message_count"])
                    if row["snapshot_customer_message_count"] is not None
                    else None
                ),
                snapshot_customer_last_message_id=str(
                    row["snapshot_customer_last_message_id"],
                ),
            )
            connection.execute(
                "UPDATE casework_effects SET authority_projection_json = ? "
                "WHERE reference = ?",
                (projection.model_dump_json(), str(row["reference"])),
            )
        connection.execute(
            "ALTER TABLE casework_effects DROP COLUMN authority_run_json"
        )

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
        authority_projection_json: str = "",
        delivery_required: bool = True,
    ) -> PendingCaseworkEffect:
        owner, reason = _validate_effect_command(
            ticket_id=ticket_id,
            delivery_attempt_id=delivery_attempt_id,
            generation=generation,
            source_event_id=source_event_id,
            reply_type=reply_type,
            body=body,
            snapshot_customer_last_message_id=snapshot_customer_last_message_id,
            ownership_action=ownership_action,
            reason_code=reason_code,
            ownership_packet=ownership_packet,
            delivery_required=delivery_required,
        )
        ownership_action = owner.value
        reason_code = reason.value
        root_delivery_attempt_id = _root_delivery_attempt_id(delivery_attempt_id)
        reference = casework_effect_reference(ticket_id, delivery_attempt_id)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            unresolved_rows = connection.execute(
                "SELECT * FROM casework_effects WHERE ticket_id = ? "
                "AND root_delivery_attempt_id = ? AND delivery_required = 1 "
                "AND public_delivery_confirmed = 0 "
                "ORDER BY created_at,reference LIMIT 2",
                (ticket_id, root_delivery_attempt_id),
            ).fetchall()
            if len(unresolved_rows) > 1:
                raise RuntimeError(
                    "multiple unresolved casework delivery descendants require "
                    "manual reconciliation"
                )
            if unresolved_rows:
                return _from_row(unresolved_rows[0])
            existing = connection.execute(
                "SELECT source_event_id,snapshot_customer_message_count,"
                "snapshot_customer_last_message_id,delivery_required,"
                "public_delivery_confirmed FROM casework_effects "
                "WHERE reference = ?",
                (reference,),
            ).fetchone()
            if (
                existing is not None
                and bool(existing["delivery_required"])
                and bool(existing["public_delivery_confirmed"])
                and (
                    str(existing["source_event_id"]) != source_event_id
                    or str(existing["snapshot_customer_last_message_id"])
                    != snapshot_customer_last_message_id
                    or (
                        existing["snapshot_customer_message_count"]
                        != snapshot_customer_message_count
                    )
                )
            ):
                delivery_attempt_id = _next_delivery_attempt_id(
                    root_delivery_attempt_id,
                    source_event_id=source_event_id,
                    snapshot_customer_message_count=(snapshot_customer_message_count),
                    snapshot_customer_last_message_id=(
                        snapshot_customer_last_message_id
                    ),
                )
                reference = casework_effect_reference(ticket_id, delivery_attempt_id)
            now = self._now()
            connection.execute(
                "INSERT OR IGNORE INTO casework_effects("
                "reference,ticket_id,delivery_attempt_id,generation,"
                "root_delivery_attempt_id,"
                "source_event_id,reply_type,body,internal_analysis,"
                "snapshot_customer_message_count,"
                "snapshot_customer_last_message_id,ownership_action,"
                "reason_code,ownership_packet,authority_projection_json,"
                "delivery_required,notification_sent,zendesk_tag_sent,"
                "created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    reference,
                    ticket_id,
                    delivery_attempt_id,
                    generation,
                    root_delivery_attempt_id,
                    source_event_id,
                    reply_type,
                    body,
                    internal_analysis,
                    snapshot_customer_message_count,
                    snapshot_customer_last_message_id,
                    ownership_action,
                    reason_code,
                    ownership_packet,
                    authority_projection_json,
                    int(delivery_required),
                    int(ownership_action == "none"),
                    int(ownership_action == "none"),
                    now,
                    now,
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
                "UPDATE casework_effects SET public_delivery_confirmed = 1,"
                "updated_at = ? "
                "WHERE reference = ?",
                (self._now(), reference),
            )
        return cursor.rowcount == 1

    def discard_definitely_unsent(self, reference: str) -> bool:
        """Retire an outer intent only when no remote effect can exist."""
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                "DELETE FROM casework_effects WHERE reference = ? "
                "AND public_delivery_confirmed = 0 "
                "AND notification_ambiguous = 0 "
                "AND (ownership_action = 'none' OR "
                "(notification_sent = 0 AND zendesk_tag_sent = 0)) "
                "AND (claim_token = '' OR lease_until <= ?)",
                (reference, self._now()),
            )
        return cursor.rowcount == 1

    def pending_ownership(self, *, limit: int = 100) -> list[PendingCaseworkEffect]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM casework_effects "
                "WHERE (delivery_required = 0 OR public_delivery_confirmed = 1) "
                "AND ownership_action != 'none' "
                "AND (notification_sent = 0 OR zendesk_tag_sent = 0) "
                "ORDER BY created_at LIMIT ?",
                (max(1, limit),),
            ).fetchall()
        return [_from_row(row) for row in rows]

    def pending_ownership_count(self) -> int:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) FROM casework_effects "
                "WHERE (delivery_required = 0 OR public_delivery_confirmed = 1) "
                "AND ownership_action != 'none' "
                "AND (notification_sent = 0 OR zendesk_tag_sent = 0)",
            ).fetchone()
        return int(row[0]) if row is not None else 0

    def claim_ownership(self, reference: str) -> str | None:
        now = self._now()
        token = secrets.token_hex(16)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                "UPDATE casework_effects SET claim_token = ?, lease_until = ? "
                "WHERE reference = ? "
                "AND (delivery_required = 0 OR public_delivery_confirmed = 1) "
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
                "updated_at = ?, "
                "notification_ambiguous = CASE "
                "WHEN ? = 'notification_sent' THEN 0 "
                "ELSE notification_ambiguous END "
                "WHERE reference = ? AND claim_token = ?",
                (self._now(), column, reference, token),
            )
        return cursor.rowcount == 1

    def begin_notification(self, reference: str, token: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE casework_effects SET notification_ambiguous = 1,"
                "updated_at = ? "
                "WHERE reference = ? AND claim_token = ?",
                (self._now(), reference, token),
            )
        return cursor.rowcount == 1

    def clear_notification_ambiguous(self, reference: str, token: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE casework_effects SET notification_ambiguous = 0,"
                "updated_at = ? "
                "WHERE reference = ? AND claim_token = ? "
                "AND notification_sent = 0",
                (self._now(), reference, token),
            )
        return cursor.rowcount == 1

    def purge_completed(
        self,
        *,
        retention_seconds: float = CASEWORK_EFFECT_RETENTION_SECONDS,
        limit: int = 100,
    ) -> int:
        """Delete only fully resolved effects after their retention window."""
        cutoff = self._now() - max(0.0, retention_seconds)
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM casework_effects WHERE reference IN ("
                "SELECT reference FROM casework_effects "
                "WHERE updated_at <= ? "
                "AND (delivery_required = 0 OR public_delivery_confirmed = 1) "
                "AND (ownership_action = 'none' OR "
                "(notification_sent = 1 AND zendesk_tag_sent = 1)) "
                "ORDER BY updated_at LIMIT ?)",
                (cutoff, max(1, limit)),
            )
        return cursor.rowcount


def _from_row(row: sqlite3.Row) -> PendingCaseworkEffect:
    effect = PendingCaseworkEffect(
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
        root_delivery_attempt_id=str(row["root_delivery_attempt_id"]),
        authority_projection_json=str(row["authority_projection_json"]),
        delivery_required=bool(row["delivery_required"]),
        public_delivery_confirmed=bool(row["public_delivery_confirmed"]),
        notification_sent=bool(row["notification_sent"]),
        notification_ambiguous=bool(row["notification_ambiguous"]),
        zendesk_tag_sent=bool(row["zendesk_tag_sent"]),
        created_at=float(row["created_at"]),
        updated_at=float(row["updated_at"]),
    )
    _validate_effect_command(
        ticket_id=effect.ticket_id,
        delivery_attempt_id=effect.delivery_attempt_id,
        generation=effect.generation,
        source_event_id=effect.source_event_id,
        reply_type=effect.reply_type,
        body=effect.body,
        snapshot_customer_last_message_id=effect.snapshot_customer_last_message_id,
        ownership_action=effect.ownership_action,
        reason_code=effect.reason_code,
        ownership_packet=effect.ownership_packet,
        delivery_required=effect.delivery_required,
    )
    return effect
