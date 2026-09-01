"""Fresh, idempotent customer delivery for Harness outcomes.

The controller is intentionally adapter-free.  PostgreSQL-backed ports can
implement the same operations transactionally, while unit tests can use tiny
in-memory fakes.  In particular, the outbox claim is the second generation
fence: a human cancellation that races with this controller must be able to
reject the claim before the Zendesk send.
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator, Awaitable, Callable, Mapping
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, field
from enum import StrEnum
from hashlib import sha256
from typing import Any, Protocol, cast

import httpx
from sqlalchemy import desc, func, select, update
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
    provider_message_id: str | None = None


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

    async def mark_sent(
        self, *, key: str, provider_message_id: str | None = None
    ) -> None: ...


class ZendeskReplyPort(Protocol):
    async def send_reply(
        self, *, ticket_id: int, body: str, idempotency_key: str
    ) -> object: ...


@dataclass(frozen=True, slots=True)
class ZendeskReplyReceipt:
    """Provider receipt persisted with a successful outbox transition."""

    provider_message_id: str

    def __post_init__(self) -> None:
        if not self.provider_message_id.strip():
            raise ValueError("provider_message_id must not be blank")


@dataclass(frozen=True, slots=True)
class ZendeskReplyConfig:
    """Credentials and endpoint for the Zendesk ticket comment API."""

    base_url: str
    email: str
    api_token: str = field(repr=False)
    timeout_seconds: float = 10.0

    def __post_init__(self) -> None:
        if any(
            not value.strip()
            for value in (self.base_url, self.email, self.api_token)
        ):
            raise ValueError("Zendesk sender configuration must not be blank")
        if self.timeout_seconds <= 0:
            raise ValueError("Zendesk sender timeout must be positive")

    @classmethod
    def from_environment(
        cls, environment: Mapping[str, str] | None = None
    ) -> ZendeskReplyConfig | None:
        values = environment if environment is not None else os.environ
        base_url = values.get("ZENDESK_BASE_URL", "").strip()
        subdomain = values.get("ZENDESK_SUBDOMAIN", "").strip()
        if not base_url and subdomain:
            base_url = f"https://{subdomain}.zendesk.com"
        email = (
            values.get("ZENDESK_ACCOUNT_EMAIL")
            or values.get("ZENDESK_EMAIL")
            or ""
        ).strip()
        api_token = values.get("ZENDESK_API_TOKEN", "").strip()
        if not base_url or not email or not api_token:
            return None
        raw_timeout = values.get("ZENDESK_TIMEOUT_SECONDS", "10").strip()
        try:
            timeout_seconds = float(raw_timeout)
        except ValueError as error:
            raise ValueError("ZENDESK_TIMEOUT_SECONDS must be numeric") from error
        return cls(
            base_url=base_url.rstrip("/"),
            email=email,
            api_token=api_token,
            timeout_seconds=timeout_seconds,
        )


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


def _receipt_provider_message_id(receipt: object) -> str | None:
    if isinstance(receipt, ZendeskReplyReceipt):
        return receipt.provider_message_id
    if isinstance(receipt, str) and receipt.strip():
        return receipt
    value = getattr(receipt, "provider_message_id", None)
    if isinstance(value, (str, int)) and str(value).strip():
        return str(value)
    return None


def _entry_provider_message_id(entry: object) -> str | None:
    return _receipt_provider_message_id(getattr(entry, "provider_message_id", None))


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


class ZendeskReplySender:
    """Send a public ticket comment through the Zendesk REST API."""

    ready = True

    def __init__(self, *, client: httpx.AsyncClient) -> None:
        self._client = client

    async def send_reply(
        self, *, ticket_id: int, body: str, idempotency_key: str
    ) -> ZendeskReplyReceipt:
        response = await self._client.put(
            f"/api/v2/tickets/{ticket_id}.json",
            headers={"Idempotency-Key": idempotency_key},
            json={
                "ticket": {
                    "comment": {
                        "body": body,
                        "public": True,
                    }
                }
            },
        )
        response.raise_for_status()
        try:
            payload = response.json()
        except ValueError as error:
            raise DeliveryUnavailableError(
                "Zendesk response was not valid JSON"
            ) from error
        provider_message_id = _provider_message_id(payload)
        if provider_message_id is None:
            raise DeliveryUnavailableError(
                "Zendesk response did not include provider message id"
            )
        return ZendeskReplyReceipt(provider_message_id=provider_message_id)

    async def aclose(self) -> None:
        await self._client.aclose()


def _provider_message_id(payload: object) -> str | None:
    """Extract a comment id without mistaking the ticket id for a receipt."""

    if not isinstance(payload, Mapping):
        return None
    data = cast(Mapping[str, object], payload)
    for field_name in ("provider_message_id", "comment_id"):
        value = data.get(field_name)
        if isinstance(value, (str, int)) and str(value).strip():
            return str(value)
    comment = data.get("comment")
    if isinstance(comment, Mapping):
        value = cast(Mapping[str, object], comment).get("id")
        if isinstance(value, (str, int)) and str(value).strip():
            return str(value)
    comments = data.get("comments")
    if isinstance(comments, list):
        for item in reversed(cast(list[object], comments)):
            if isinstance(item, Mapping):
                value = cast(Mapping[str, object], item).get("id")
                if isinstance(value, (str, int)) and str(value).strip():
                    return str(value)
    ticket = data.get("ticket")
    if isinstance(ticket, Mapping):
        return _provider_message_id(cast(Mapping[str, object], ticket))
    return None


async def _close_unavailable_sender() -> None:
    return None


def create_zendesk_reply_sender(
    environment: Mapping[str, str] | None = None,
) -> tuple[ZendeskReplyPort, Callable[[], Awaitable[None]]]:
    """Build a sender without making a network request.

    Missing or incomplete credentials return the explicit unavailable sender;
    callers can expose that state through readiness and remain fail-closed.
    """

    config = ZendeskReplyConfig.from_environment(environment)
    if config is None:
        return UnavailableZendeskReplySender(), _close_unavailable_sender
    client = httpx.AsyncClient(
        base_url=config.base_url,
        auth=(f"{config.email}/token", config.api_token),
        timeout=config.timeout_seconds,
    )
    sender = ZendeskReplySender(client=client)
    return sender, sender.aclose


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
            self._policy.mode in {DeliveryMode.LIVE, DeliveryMode.CANARY}
            and getattr(self._sender, "ready", True) is False
        ):
            return DeliveryResult(
                disposition=DeliveryDisposition.FAILED,
                ticket_id=ticket_id,
                reason=f"{self._policy.mode.value}_sender_not_ready",
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
                provider_message_id=_entry_provider_message_id(entry),
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
            receipt = await self._sender.send_reply(
                ticket_id=ticket_id,
                body=rendered,
                idempotency_key=idempotency_key,
            )
        except Exception as error:
            await self._mark_ambiguous(outbox_key, error=str(error))
            return DeliveryResult(
                disposition=DeliveryDisposition.FAILED,
                ticket_id=ticket_id,
                reason="reply_send_failed",
                outbox_key=outbox_key,
                idempotency_key=idempotency_key,
                body=rendered,
            )
        provider_message_id = _receipt_provider_message_id(receipt)
        await self._outbox.mark_sent(
            key=outbox_key, provider_message_id=provider_message_id
        )
        return DeliveryResult(
            disposition=DeliveryDisposition.SENT,
            ticket_id=ticket_id,
            reason="reply_sent",
            outbox_key=outbox_key,
            idempotency_key=idempotency_key,
            body=rendered,
            provider_message_id=provider_message_id,
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

    async def _mark_ambiguous(
        self, key: str, *, error: str = "reply_send_failed"
    ) -> None:
        marker = getattr(self._outbox, "mark_ambiguous", None)
        if marker is not None:
            await cast(ProtocolCallable, marker)(key=key, error=error)

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


def resolve_delivery_mode(environment: Mapping[str, str] | None = None) -> DeliveryMode:
    """Resolve the explicit deployment mode, with legacy env compatibility."""

    values = environment if environment is not None else os.environ
    return parse_delivery_mode(
        values.get("DELIVERY_MODE") or values.get("CASEWORK_DELIVERY_MODE")
    )


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
                    updated_at=func.now(),
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
                .values(
                    status="cancelled",
                    last_error=reason,
                    updated_at=func.now(),
                )
            )

    async def mark_sent(
        self, *, key: str, provider_message_id: str | None = None
    ) -> None:
        async with self._transaction_scope() as session:
            await session.execute(
                update(OutboxMessage)
                .where(
                    OutboxMessage.outbox_id == key,
                    OutboxMessage.status == "attempted",
                )
                .values(
                    status="sent",
                    provider_message_id=provider_message_id,
                    updated_at=func.now(),
                )
            )

    async def reconcile(
        self, *, key: str, provider_message_id: str
    ) -> bool:
        if not provider_message_id.strip():
            raise ValueError("provider_message_id must not be blank")
        async with self._transaction_scope() as session:
            result = await session.execute(
                update(OutboxMessage)
                .where(
                    OutboxMessage.outbox_id == key,
                    OutboxMessage.status.in_(
                        ("ambiguous", "attempted", "sent")
                    ),
                )
                .values(
                    status="confirmed",
                    provider_message_id=provider_message_id,
                    updated_at=func.now(),
                )
            )
            return bool(getattr(result, "rowcount", 0))

    async def mark_confirmed(
        self, *, key: str, provider_message_id: str
    ) -> bool:
        """Compatibility alias for receipt reconciliation workers."""

        return await self.reconcile(
            key=key, provider_message_id=provider_message_id
        )

    async def mark_ambiguous(
        self, *, key: str, error: str = "reply_send_failed"
    ) -> bool:
        async with self._transaction_scope() as session:
            result = await session.execute(
                update(OutboxMessage)
                .where(
                    OutboxMessage.outbox_id == key,
                    OutboxMessage.status == "attempted",
                )
                .values(
                    status="ambiguous",
                    last_error=error,
                    updated_at=func.now(),
                )
            )
            return bool(getattr(result, "rowcount", 0))

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
    "ZendeskReplyConfig",
    "ZendeskReplyPort",
    "ZendeskReplyReceipt",
    "ZendeskReplySender",
    "create_zendesk_reply_sender",
    "parse_delivery_mode",
    "postgres_delivery_transaction_scope",
    "resolve_delivery_mode",
]
