"""Fresh, idempotent customer delivery for Harness outcomes.

The controller is intentionally adapter-free.  PostgreSQL-backed ports can
implement the same operations transactionally, while unit tests can use tiny
in-memory fakes.  In particular, the outbox claim is the second generation
fence: a human cancellation that races with this controller must be able to
reject the claim before the Zendesk send.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, cast

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
    mode: DeliveryMode = DeliveryMode.LIVE
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
        body: str,
        idempotency_key: str,
    ) -> object: ...

    async def claim(self, *, key: str, generation: int, cursor: str) -> bool: ...

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
        return (
            f"ticket:{ticket_id}:generation:{generation}:cursor:{snapshot_cursor}"
        )


class ProtocolCallable(Protocol):
    async def __call__(self, *, key: str) -> None: ...


__all__ = [
    "DeliveryController",
    "DeliveryDisposition",
    "DeliveryMode",
    "DeliveryOutboxPort",
    "DeliveryPolicy",
    "DeliveryRequest",
    "DeliveryResult",
    "ProtocolCallable",
    "TicketFreshness",
    "TicketFreshnessPort",
    "ZendeskReplyPort",
]
