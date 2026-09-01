"""Fresh, idempotent customer delivery for Harness outcomes.

The controller is intentionally adapter-free.  PostgreSQL-backed ports can
implement the same operations transactionally, while unit tests can use tiny
in-memory fakes.  In particular, the outbox claim is the second generation
fence: a human cancellation that races with this controller must be able to
reject the claim before the Zendesk send.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from typing import Any, Protocol, cast

from sqlalchemy import desc, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from casework_persistence.models import EventRecord, OutboxMessage, TicketState
from casework_persistence.repositories import (
    OutboxDraft,
    OutboxRepository,
    SessionPort,
)
from contracts.outcome import Outcome, OutcomeKind

from .footer import FooterCatalog, append_localized_footer


class DeliveryDisposition(StrEnum):
    SENT = "sent"
    DRY_RUN = "dry_run"
    DEDUPLICATED = "deduplicated"
    STALE = "stale"
    FENCED = "fenced"
    NOT_ELIGIBLE = "not_eligible"
    FAILED = "failed"


class DeliveryMode(StrEnum):
    LIVE = "live"
    DRY_RUN = "dry_run"
    CANARY = "canary"


@dataclass(frozen=True, slots=True)
class DeliveryPolicy:
    mode: DeliveryMode = DeliveryMode.DRY_RUN
    canary_ticket_ids: frozenset[int] = frozenset()

    def __post_init__(self) -> None:
        if any(ticket_id <= 0 for ticket_id in self.canary_ticket_ids):
            raise ValueError("canary ticket identifiers must be positive")
        if self.mode is DeliveryMode.CANARY and not self.canary_ticket_ids:
            raise ValueError("canary mode requires explicit ticket identifiers")


@dataclass(frozen=True, slots=True)
class DeliveryRequest:
    """The immutable delivery inputs captured by one Harness run."""

    outcome: Outcome
    generation: int
    snapshot_cursor: str | None = None
    locale: str | None = None

    def __post_init__(self) -> None:
        if self.generation < 1:
            raise ValueError("generation must be positive")
        if self.snapshot_cursor is not None and not self.snapshot_cursor.strip():
            raise ValueError("snapshot_cursor must not be empty")
        if (
            self.snapshot_cursor is not None
            and self.outcome.snapshot_cursor is not None
            and self.snapshot_cursor != self.outcome.snapshot_cursor
        ):
            raise ValueError("request snapshot_cursor does not match outcome")
        if (
            self.locale is not None
            and self.outcome.reply_locale is not None
            and self.locale != self.outcome.reply_locale
        ):
            raise ValueError("request locale does not match outcome")


@dataclass(frozen=True, slots=True)
class TicketFreshness:
    """Current ticket cursor and cancellation generation from the source DB."""

    generation: int
    cursor: str


@dataclass(frozen=True, slots=True)
class DeliveryResult:
    disposition: DeliveryDisposition
    ticket_id: int
    reason: str
    outbox_key: str | None = None
    idempotency_key: str | None = None
    body: str | None = None


class TicketFreshnessPort(Protocol):
    """Read the latest customer cursor and human-cancellation generation."""

    async def latest(self, ticket_id: int) -> TicketFreshness | tuple[int, str]: ...


class DeliveryOutboxPort(Protocol):
    """Transactional outbox operations.

    ``claim`` must atomically re-check the generation and cursor in the
    durable implementation.  This is what closes the human-vs-send race after
    the controller's read-before-create check.
    """

    async def create_or_get(
        self,
        *,
        key: str,
        ticket_id: int,
        generation: int,
        cursor: str,
        locale: str,
        body: str,
        idempotency_key: str,
    ) -> object: ...

    async def claim(
        self, *, key: str, ticket_id: int, generation: int, cursor: str
    ) -> bool: ...

    async def cancel(self, *, key: str, reason: str) -> None: ...

    async def mark_sent(self, *, key: str) -> None: ...


class ZendeskReplyPort(Protocol):
    async def send_reply(
        self, *, ticket_id: int, body: str, idempotency_key: str
    ) -> object: ...


def _outcome_body(outcome: Outcome) -> str | None:
    if outcome.kind is OutcomeKind.ANSWER:
        return outcome.answer
    if outcome.kind is OutcomeKind.ASK_CUSTOMER:
        return outcome.question
    return None


def _freshness(value: TicketFreshness | tuple[int, str]) -> TicketFreshness:
    if isinstance(value, TicketFreshness):
        return value
    generation, cursor = value
    return TicketFreshness(generation=int(generation), cursor=str(cursor))


def _entry_status(entry: object) -> str:
    status = getattr(entry, "status", "pending")
    return getattr(status, "value", str(status)).casefold()


class DeliveryUnavailableError(RuntimeError):
    """Raised when live delivery was requested without a real sender client."""


class UnavailableZendeskReplySender:
    """Fail-closed placeholder until a real Zendesk sender is configured.

    This class deliberately performs no network operation.  It lets the
    production composition expose an unhealthy readiness state while keeping
    accidental live writes impossible.
    """

    ready = False

    async def send_reply(
        self, *, ticket_id: int, body: str, idempotency_key: str
    ) -> object:
        raise DeliveryUnavailableError(
            "live Zendesk sender is not configured; no customer write performed"
        )


class DeliveryController:
    """Deliver only customer-facing answer/ask outcomes.

    The sequence is:

    1. reject non-deliverable outcomes without touching the outbox;
    2. check current cursor and human generation;
    3. create/get the deterministic outbox row;
    4. check freshness again and atomically claim the row;
    5. send once with a stable idempotency key and mark it sent.
    """

    def __init__(
        self,
        *,
        freshness: TicketFreshnessPort,
        outbox: DeliveryOutboxPort,
        sender: ZendeskReplyPort,
        footer_catalog: FooterCatalog,
        policy: DeliveryPolicy | None = None,
    ) -> None:
        self._freshness = freshness
        self._outbox = outbox
        self._sender = sender
        self._footer_catalog = footer_catalog
        self._policy = policy or DeliveryPolicy()

    async def deliver(self, request: DeliveryRequest) -> DeliveryResult:
        outcome = request.outcome
        ticket_id = outcome.ticket_id
        body = _outcome_body(outcome)
        if body is None or not outcome.delivery_eligible:
            return DeliveryResult(
                disposition=DeliveryDisposition.NOT_ELIGIBLE,
                ticket_id=ticket_id,
                reason=(
                    f"outcome_{outcome.kind.value}_does_not_create_reply"
                    if body is None
                    else "outcome_delivery_not_eligible"
                ),
            )

        if (
            self._policy.mode is DeliveryMode.CANARY
            and ticket_id not in self._policy.canary_ticket_ids
        ):
            return DeliveryResult(
                disposition=DeliveryDisposition.NOT_ELIGIBLE,
                ticket_id=ticket_id,
                reason="ticket_not_in_canary_allowlist",
            )
        if (
            self._policy.mode is DeliveryMode.LIVE
            and getattr(self._sender, "ready", True) is False
        ):
            return DeliveryResult(
                disposition=DeliveryDisposition.FAILED,
                ticket_id=ticket_id,
                reason="live_sender_not_ready",
            )

        # Eligible outcomes carry the source-of-truth metadata.  The request
        # wrapper may omit duplicate values, but may never override them.
        snapshot_cursor = outcome.snapshot_cursor or request.snapshot_cursor
        locale = outcome.reply_locale or request.locale
        if not snapshot_cursor or not locale:
            return DeliveryResult(
                disposition=DeliveryDisposition.NOT_ELIGIBLE,
                ticket_id=ticket_id,
                reason="eligible_outcome_missing_delivery_metadata",
            )

        current = await self._read_current(ticket_id)
        mismatch = self._mismatch(
            current, request, snapshot_cursor=snapshot_cursor
        )
        if mismatch is not None:
            return self._mismatch_result(ticket_id, mismatch)

        rendered = append_localized_footer(
            body,
            locale=locale,
            catalog=self._footer_catalog,
        )
        if self._policy.mode is DeliveryMode.DRY_RUN:
            return DeliveryResult(
                disposition=DeliveryDisposition.DRY_RUN,
                ticket_id=ticket_id,
                reason="dry_run_no_write",
                body=rendered,
            )
        if self._policy.mode is DeliveryMode.CANARY:
            return DeliveryResult(
                disposition=DeliveryDisposition.DRY_RUN,
                ticket_id=ticket_id,
                reason="canary_no_write",
                body=rendered,
            )
        outbox_key = self._outbox_key(
            ticket_id=ticket_id,
            generation=request.generation,
            snapshot_cursor=snapshot_cursor,
        )
        idempotency_key = outbox_key
        entry = await self._outbox.create_or_get(
            key=outbox_key,
            ticket_id=ticket_id,
            generation=request.generation,
            cursor=snapshot_cursor,
            locale=locale,
            body=rendered,
            idempotency_key=idempotency_key,
        )
        if _entry_status(entry) in {"sent", "confirmed", "attempted", "ambiguous"}:
            return DeliveryResult(
                disposition=DeliveryDisposition.DEDUPLICATED,
                ticket_id=ticket_id,
                reason="outbox_already_claimed",
                outbox_key=outbox_key,
                idempotency_key=idempotency_key,
                body=rendered,
            )

        # A human or a new customer message can arrive while the outbox row is
        # being created.  Read once more immediately before the transactional
        # claim; the port's claim must repeat this check atomically.
        current = await self._read_current(ticket_id)
        mismatch = self._mismatch(
            current, request, snapshot_cursor=snapshot_cursor
        )
        if mismatch is not None:
            await self._outbox.cancel(key=outbox_key, reason=mismatch)
            return self._mismatch_result(
                ticket_id,
                mismatch,
                outbox_key=outbox_key,
                idempotency_key=idempotency_key,
                body=rendered,
            )

        claimed = await self._outbox.claim(
            key=outbox_key,
            ticket_id=ticket_id,
            generation=request.generation,
            cursor=snapshot_cursor,
        )
        if not claimed:
            await self._outbox.cancel(
                key=outbox_key, reason="generation_or_cursor_fence"
            )
            return DeliveryResult(
                disposition=DeliveryDisposition.FENCED,
                ticket_id=ticket_id,
                reason="outbox_claim_rejected_by_fence",
                outbox_key=outbox_key,
                idempotency_key=idempotency_key,
                body=rendered,
            )

        try:
            await self._mark_attempted(outbox_key)
            await self._sender.send_reply(
                ticket_id=ticket_id,
                body=rendered,
                idempotency_key=idempotency_key,
            )
        except Exception:
            await self._mark_ambiguous(outbox_key)
            return DeliveryResult(
                disposition=DeliveryDisposition.FAILED,
                ticket_id=ticket_id,
                reason="reply_send_failed",
                outbox_key=outbox_key,
                idempotency_key=idempotency_key,
                body=rendered,
            )
        await self._outbox.mark_sent(key=outbox_key)
        return DeliveryResult(
            disposition=DeliveryDisposition.SENT,
            ticket_id=ticket_id,
            reason="reply_sent",
            outbox_key=outbox_key,
            idempotency_key=idempotency_key,
            body=rendered,
        )

    async def _read_current(self, ticket_id: int) -> TicketFreshness:
        return _freshness(await self._freshness.latest(ticket_id))

    @staticmethod
    def _mismatch(
        current: TicketFreshness,
        request: DeliveryRequest,
        *,
        snapshot_cursor: str,
    ) -> str | None:
        if current.generation != request.generation:
            return "human_generation_fence"
        if current.cursor != snapshot_cursor:
            return "latest_customer_cursor_changed"
        return None

    @staticmethod
    def _mismatch_result(
        ticket_id: int,
        reason: str,
        *,
        outbox_key: str | None = None,
        idempotency_key: str | None = None,
        body: str | None = None,
    ) -> DeliveryResult:
        disposition = (
            DeliveryDisposition.FENCED
            if reason == "human_generation_fence"
            else DeliveryDisposition.STALE
        )
        return DeliveryResult(
            disposition=disposition,
            ticket_id=ticket_id,
            reason=reason,
            outbox_key=outbox_key,
            idempotency_key=idempotency_key,
            body=body,
        )

    async def _mark_attempted(self, key: str) -> None:
        marker = getattr(self._outbox, "mark_attempted", None)
        if marker is not None:
            await cast(ProtocolCallable, marker)(key=key)

    async def _mark_ambiguous(self, key: str) -> None:
        marker = getattr(self._outbox, "mark_ambiguous", None)
        if marker is not None:
            await cast(ProtocolCallable, marker)(key=key)

    @staticmethod
    def _outbox_key(
        *, ticket_id: int, generation: int, snapshot_cursor: str
    ) -> str:
        material = f"{ticket_id}:{generation}:{snapshot_cursor}".encode()
        digest = sha256(material).hexdigest()
        return f"ticket:{ticket_id}:generation:{generation}:cursor-sha256:{digest}"


DeliveryTransactionScope = Callable[
    [], AbstractAsyncContextManager[SessionPort]
]


def postgres_delivery_transaction_scope(
    sessions: async_sessionmaker[AsyncSession],
) -> DeliveryTransactionScope:
    """Create a transaction scope for durable freshness and outbox writes."""

    @asynccontextmanager
    async def scope() -> AsyncGenerator[SessionPort, None]:
        async with sessions() as session, session.begin():
            yield cast(SessionPort, session)

    return scope


def parse_delivery_mode(value: str | None) -> DeliveryMode:
    """Parse the explicit deployment mode, defaulting safely to dry-run."""

    raw = (value or DeliveryMode.DRY_RUN.value).strip().casefold().replace("-", "_")
    try:
        return DeliveryMode(raw)
    except ValueError as error:
        raise ValueError(
            "delivery mode must be one of: dry_run, live, canary"
        ) from error


class PostgresDeliveryAdapter:
    """Durable freshness fence and transactional outbox adapter.

    Every operation runs inside the injected PostgreSQL transaction scope.  A
    claim locks the ticket state, re-reads the newest customer event, and only
    then changes the matching pending row to ``attempted``.  Human cancellation
    uses the same ticket-state lock and therefore cannot race a stale claim.
    """

    def __init__(self, *, transaction_scope: DeliveryTransactionScope) -> None:
        self._transaction_scope = transaction_scope

    async def latest(self, ticket_id: int) -> TicketFreshness:
        async with self._transaction_scope() as session:
            generation_result = await session.execute(
                select(TicketState.generation).where(
                    TicketState.ticket_id == ticket_id
                )
            )
            generation = generation_result.scalar_one_or_none()
            event = await self._latest_customer_event(session, ticket_id)
        return TicketFreshness(
            generation=int(generation) if generation is not None else 1,
            cursor=self._event_cursor(event),
        )

    async def create_or_get(
        self,
        *,
        key: str,
        ticket_id: int,
        generation: int,
        cursor: str,
        locale: str,
        body: str,
        idempotency_key: str,
    ) -> object:
        async with self._transaction_scope() as session:
            return await OutboxRepository(session).create(
                OutboxDraft(
                    outbox_id=key,
                    job_id=None,
                    ticket_id=ticket_id,
                    generation=generation,
                    snapshot_cursor=cursor,
                    locale=locale,
                    body=body,
                    idempotency_key=idempotency_key,
                )
            )

    async def claim(
        self, *, key: str, ticket_id: int, generation: int, cursor: str
    ) -> bool:
        async with self._transaction_scope() as session:
            # HumanCancelRepository locks and increments this same row.  The
            # row lock plus the checks below make this an atomic generation and
            # latest-customer-cursor fence in PostgreSQL.
            state_result = await session.execute(
                select(TicketState.generation)
                .where(TicketState.ticket_id == ticket_id)
                .with_for_update()
            )
            current_generation = state_result.scalar_one_or_none()
            if current_generation is None or int(current_generation) != generation:
                return False
            event = await self._latest_customer_event(session, ticket_id)
            if self._event_cursor(event) != cursor:
                return False
            result = await session.execute(
                update(OutboxMessage)
                .where(
                    OutboxMessage.outbox_id == key,
                    OutboxMessage.ticket_id == ticket_id,
                    OutboxMessage.generation == generation,
                    OutboxMessage.snapshot_cursor == cursor,
                    OutboxMessage.status == "pending",
                )
                .values(
                    status="attempted",
                    attempt_count=OutboxMessage.attempt_count + 1,
                )
            )
            return bool(getattr(result, "rowcount", 0))

    async def cancel(self, *, key: str, reason: str) -> None:
        async with self._transaction_scope() as session:
            await session.execute(
                update(OutboxMessage)
                .where(
                    OutboxMessage.outbox_id == key,
                    OutboxMessage.status.in_(("pending", "attempted", "ambiguous")),
                )
                .values(status="cancelled", last_error=reason)
            )

    async def mark_sent(self, *, key: str) -> None:
        async with self._transaction_scope() as session:
            await session.execute(
                update(OutboxMessage)
                .where(
                    OutboxMessage.outbox_id == key,
                    OutboxMessage.status == "attempted",
                )
                .values(status="sent")
            )

    async def mark_ambiguous(
        self, *, key: str, error: str = "reply_send_failed"
    ) -> None:
        async with self._transaction_scope() as session:
            await session.execute(
                update(OutboxMessage)
                .where(
                    OutboxMessage.outbox_id == key,
                    OutboxMessage.status == "attempted",
                )
                .values(status="ambiguous", last_error=error)
            )

    @staticmethod
    async def _latest_customer_event(
        session: SessionPort, ticket_id: int
    ) -> EventRecord | None:
        result = await session.execute(
            select(EventRecord)
            .where(
                EventRecord.ticket_id == ticket_id,
                EventRecord.customer_origin == "customer",
            )
            .order_by(desc(EventRecord.occurred_at), desc(EventRecord.event_id))
            .limit(1)
        )
        return result.scalar_one_or_none()

    @staticmethod
    def _event_cursor(event: EventRecord | None) -> str:
        if event is None:
            return ""
        payload: dict[str, Any] = event.payload
        for field_name in ("snapshot_cursor", "cursor", "message_id"):
            value = payload.get(field_name)
            if isinstance(value, str) and value.strip():
                return value
        if event.message_id and event.message_id.strip():
            return event.message_id
        return event.event_id


class ControllerDeliveryAdapter:
    """Adapt the controller request shape to the worker delivery port."""

    def __init__(self, controller: DeliveryController) -> None:
        self._controller = controller

    async def deliver(self, outcome: Outcome, generation: int) -> DeliveryResult:
        return await self._controller.deliver(
            DeliveryRequest(outcome=outcome, generation=generation)
        )


class ProtocolCallable(Protocol):
    async def __call__(self, *, key: str, error: str = "reply_send_failed") -> None: ...


__all__ = [
    "ControllerDeliveryAdapter",
    "DeliveryController",
    "DeliveryDisposition",
    "DeliveryMode",
    "DeliveryOutboxPort",
    "DeliveryPolicy",
    "DeliveryRequest",
    "DeliveryResult",
    "DeliveryTransactionScope",
    "DeliveryUnavailableError",
    "PostgresDeliveryAdapter",
    "ProtocolCallable",
    "TicketFreshness",
    "TicketFreshnessPort",
    "UnavailableZendeskReplySender",
    "ZendeskReplyPort",
    "parse_delivery_mode",
    "postgres_delivery_transaction_scope",
]
