from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

import pytest

from casework_harness.delivery import (
    DeliveryController,
    DeliveryDisposition,
    DeliveryRequest,
)
from casework_harness.footer import (
    CANONICAL_FOOTER_MARKER,
    FooterCatalog,
    append_localized_footer,
)
from contracts.outcome import Outcome, OutcomeKind


def _new_entries() -> dict[str, FakeOutboxEntry]:
    return {}


def _new_strings() -> list[str]:
    return []


def _new_sent() -> list[tuple[int, str, str]]:
    return []


def _outcome(
    kind: OutcomeKind,
    *,
    answer: str | None = None,
    question: str | None = None,
) -> Outcome:
    return Outcome(
        outcome_id=f"out-{kind.value}",
        session_id="session-3726",
        ticket_id=3726,
        kind=kind,
        answer=answer,
        question=question,
        reason=(
            "not eligible"
            if kind not in {OutcomeKind.ANSWER, OutcomeKind.ASK}
            else None
        ),
        reply_locale="ja-JP" if kind in {OutcomeKind.ANSWER, OutcomeKind.ASK} else None,
        snapshot_cursor=(
            "customer-01" if kind in {OutcomeKind.ANSWER, OutcomeKind.ASK} else None
        ),
        delivery_eligible=kind in {OutcomeKind.ANSWER, OutcomeKind.ASK},
        evidence_ids=("ev-01",) if kind is OutcomeKind.ANSWER else (),
    )


def _catalog() -> FooterCatalog:
    return FooterCatalog(
        default_locale="en-US",
        footers={
            "en-US": "This reply was sent by Dify AI Agent.",
            "ja-JP": "この返信は Dify AI Agent によって送信されました。",
        },
    )


def test_footer_uses_canonical_marker_and_appends_once() -> None:
    catalog = _catalog()

    rendered = append_localized_footer("回答本文", locale="ja-JP", catalog=catalog)
    rendered_again = append_localized_footer(
        rendered, locale="ja-JP", catalog=catalog
    )

    assert rendered == rendered_again
    assert rendered.count(CANONICAL_FOOTER_MARKER) == 1
    assert rendered.endswith("この返信は Dify AI Agent によって送信されました。")


def test_footer_replaces_old_localized_footer_instead_of_nesting_it() -> None:
    catalog = _catalog()
    old = append_localized_footer("本文", locale="en-US", catalog=catalog)

    rendered = append_localized_footer(old, locale="ja-JP", catalog=catalog)

    assert rendered.count(CANONICAL_FOOTER_MARKER) == 1
    assert "This reply was sent" not in rendered
    assert rendered.startswith("本文")
    assert rendered.endswith("この返信は Dify AI Agent によって送信されました。")


def test_unsupported_locale_falls_back_to_configured_default() -> None:
    rendered = append_localized_footer(
        "Body", locale="pt-BR", catalog=_catalog()
    )

    assert rendered.endswith("This reply was sent by Dify AI Agent.")


@dataclass
class FakeFreshness:
    generation: int = 7
    cursor: str = "customer-01"
    reads: int = 0
    human_after_first_read: bool = False
    first_read: asyncio.Event = field(default_factory=asyncio.Event)
    release_first_read: asyncio.Event = field(default_factory=asyncio.Event)

    async def latest(self, ticket_id: int) -> tuple[int, str]:
        self.reads += 1
        if self.human_after_first_read and self.reads == 1:
            result = (self.generation, self.cursor)
            self.first_read.set()
            await self.release_first_read.wait()
            return result
        return self.generation, self.cursor


@dataclass(frozen=True)
class FakeOutboxEntry:
    key: str
    ticket_id: int
    generation: int
    cursor: str
    body: str
    idempotency_key: str
    status: str = "pending"


@dataclass
class FakeOutbox:
    entries: dict[str, FakeOutboxEntry] = field(default_factory=_new_entries)
    claimed: set[str] = field(default_factory=set[str])
    cancelled: list[str] = field(default_factory=_new_strings)
    sent: list[str] = field(default_factory=_new_strings)

    async def create_or_get(
        self,
        *,
        key: str,
        ticket_id: int,
        generation: int,
        cursor: str,
        body: str,
        idempotency_key: str,
    ) -> FakeOutboxEntry:
        entry = self.entries.get(key)
        if entry is None:
            entry = FakeOutboxEntry(
                key=key,
                ticket_id=ticket_id,
                generation=generation,
                cursor=cursor,
                body=body,
                idempotency_key=idempotency_key,
            )
            self.entries[key] = entry
        return entry

    async def claim(self, *, key: str, generation: int, cursor: str) -> bool:
        entry = self.entries[key]
        if entry.generation != generation or entry.cursor != cursor:
            return False
        if entry.status != "pending" or key in self.claimed:
            return False
        self.claimed.add(key)
        return True

    async def mark_sent(self, *, key: str) -> None:
        self.sent.append(key)
        entry = self.entries[key]
        self.entries[key] = FakeOutboxEntry(
            key=entry.key,
            ticket_id=entry.ticket_id,
            generation=entry.generation,
            cursor=entry.cursor,
            body=entry.body,
            idempotency_key=entry.idempotency_key,
            status="sent",
        )

    async def cancel(self, *, key: str, reason: str) -> None:
        self.cancelled.append(f"{key}:{reason}")


@dataclass
class FakeSender:
    sent: list[tuple[int, str, str]] = field(default_factory=_new_sent)
    before_send: Callable[[], Awaitable[None]] | None = None

    async def send_reply(
        self, *, ticket_id: int, body: str, idempotency_key: str
    ) -> None:
        if self.before_send is not None:
            callback = self.before_send
            self.before_send = None
            await callback()
        self.sent.append((ticket_id, body, idempotency_key))


def _request(
    outcome: Outcome | None = None,
    *,
    generation: int = 7,
    cursor: str = "customer-01",
) -> DeliveryRequest:
    return DeliveryRequest(
        outcome=outcome or _outcome(OutcomeKind.ANSWER, answer="Answer"),
        generation=generation,
        snapshot_cursor=cursor,
        locale="ja-JP",
    )


@pytest.mark.asyncio
async def test_answer_delivery_adds_japanese_footer_and_is_idempotent() -> None:
    freshness = FakeFreshness()
    outbox = FakeOutbox()
    sender = FakeSender()
    controller = DeliveryController(
        freshness=freshness,
        outbox=outbox,
        sender=sender,
        footer_catalog=_catalog(),
    )
    request = _request()

    first = await controller.deliver(request)
    second = await controller.deliver(request)

    assert first.disposition is DeliveryDisposition.SENT
    assert second.disposition is DeliveryDisposition.DEDUPLICATED
    assert len(sender.sent) == 1
    assert sender.sent[0][1].count(CANONICAL_FOOTER_MARKER) == 1
    assert sender.sent[0][1].endswith(
        "この返信は Dify AI Agent によって送信されました。"
    )


@pytest.mark.asyncio
async def test_new_customer_message_invalidates_draft_before_outbox_creation() -> None:
    freshness = FakeFreshness(cursor="customer-02")
    outbox = FakeOutbox()
    sender = FakeSender()
    controller = DeliveryController(
        freshness=freshness,
        outbox=outbox,
        sender=sender,
        footer_catalog=_catalog(),
    )

    result = await controller.deliver(_request(cursor="customer-01"))

    assert result.disposition is DeliveryDisposition.STALE
    assert outbox.entries == {}
    assert sender.sent == []


@pytest.mark.asyncio
async def test_answer_kind_alone_is_not_delivery_authorization() -> None:
    outbox = FakeOutbox()
    freshness = FakeFreshness()
    controller = DeliveryController(
        freshness=freshness,
        outbox=outbox,
        sender=FakeSender(),
        footer_catalog=_catalog(),
    )
    outcome = Outcome(
        outcome_id="out-draft",
        session_id="session-3726",
        ticket_id=3726,
        kind=OutcomeKind.ANSWER,
        answer="Unreviewed draft",
        evidence_ids=("ev-01",),
        reply_locale="ja-JP",
        snapshot_cursor="customer-01",
        delivery_eligible=False,
    )

    result = await controller.deliver(
        DeliveryRequest(outcome=outcome, generation=7)
    )

    assert result.disposition is DeliveryDisposition.NOT_ELIGIBLE
    assert freshness.reads == 0
    assert outbox.entries == {}


@pytest.mark.asyncio
async def test_human_generation_fence_wins_when_human_arrives_concurrently() -> None:
    freshness = FakeFreshness()
    outbox = FakeOutbox()
    sender = FakeSender()

    # The human command is allowed to land while the first freshness read is
    # in flight; the second read is the just-before-send fence.
    freshness.human_after_first_read = True
    controller = DeliveryController(
        freshness=freshness,
        outbox=outbox,
        sender=sender,
        footer_catalog=_catalog(),
    )

    async def human_event() -> None:
        await freshness.first_read.wait()
        freshness.generation = 8
        freshness.release_first_read.set()

    results = await asyncio.gather(
        controller.deliver(_request()), human_event()
    )
    result = results[0]

    assert result.disposition is DeliveryDisposition.FENCED
    assert sender.sent == []
    assert outbox.cancelled


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind",
    [OutcomeKind.FAILED, OutcomeKind.CANCELLED, OutcomeKind.HUMAN_TAKEOVER],
)
async def test_non_customer_terminal_outcomes_create_no_outbox(
    kind: OutcomeKind,
) -> None:
    outbox = FakeOutbox()
    controller = DeliveryController(
        freshness=FakeFreshness(),
        outbox=outbox,
        sender=FakeSender(),
        footer_catalog=_catalog(),
    )

    result = await controller.deliver(_request(_outcome(kind)))

    assert result.disposition is DeliveryDisposition.NOT_ELIGIBLE
    assert outbox.entries == {}


@pytest.mark.asyncio
async def test_concurrent_delivery_claims_only_one_outbox_send() -> None:
    outbox = FakeOutbox()
    sender = FakeSender()
    controller = DeliveryController(
        freshness=FakeFreshness(),
        outbox=outbox,
        sender=sender,
        footer_catalog=_catalog(),
    )

    results = await asyncio.gather(
        controller.deliver(_request()), controller.deliver(_request())
    )

    assert sorted(result.disposition for result in results) == [
        DeliveryDisposition.DEDUPLICATED,
        DeliveryDisposition.SENT,
    ]
    assert len(sender.sent) == 1
