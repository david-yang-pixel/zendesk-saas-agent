from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

import pytest

from casework_harness.delivery import (
    DeliveryController,
    DeliveryDisposition,
    DeliveryMode,
    DeliveryPolicy,
    DeliveryRequest,
    FrozenDeliveryConfig,
    UnavailableZendeskReplySender,
    ZendeskReplyReceipt,
    parse_delivery_mode,
    resolve_delivery_mode,
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


def test_delivery_defaults_to_dry_run_and_mode_parser_is_explicit() -> None:
    assert DeliveryPolicy().mode is DeliveryMode.DRY_RUN
    assert parse_delivery_mode(None) is DeliveryMode.DRY_RUN
    assert parse_delivery_mode("live") is DeliveryMode.LIVE
    assert parse_delivery_mode("dry-run") is DeliveryMode.DRY_RUN
    assert resolve_delivery_mode({"DELIVERY_MODE": "canary"}) is DeliveryMode.CANARY
    with pytest.raises(ValueError, match="delivery mode"):
        parse_delivery_mode("send-everything")


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


@dataclass
class FakeDeliveryConfigLoader:
    configs: dict[str, FrozenDeliveryConfig]
    revisions: list[str] = field(default_factory=list[str])

    async def load(self, *, config_revision: str) -> FrozenDeliveryConfig:
        self.revisions.append(config_revision)
        return self.configs[config_revision]


@dataclass(frozen=True)
class FakeOutboxEntry:
    key: str
    ticket_id: int
    generation: int
    cursor: str
    locale: str
    body: str
    idempotency_key: str
    status: str = "pending"
    provider_message_id: str | None = None


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
        locale: str,
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
                locale=locale,
                body=body,
                idempotency_key=idempotency_key,
            )
            self.entries[key] = entry
        return entry

    async def claim(
        self, *, key: str, ticket_id: int, generation: int, cursor: str
    ) -> bool:
        entry = self.entries[key]
        if (
            entry.ticket_id != ticket_id
            or entry.generation != generation
            or entry.cursor != cursor
        ):
            return False
        if entry.status != "pending" or key in self.claimed:
            return False
        self.claimed.add(key)
        return True

    async def mark_sent(
        self, *, key: str, provider_message_id: str | None = None
    ) -> None:
        self.sent.append(key)
        entry = self.entries[key]
        self.entries[key] = FakeOutboxEntry(
            key=entry.key,
            ticket_id=entry.ticket_id,
            generation=entry.generation,
            cursor=entry.cursor,
            locale=entry.locale,
            body=entry.body,
            idempotency_key=entry.idempotency_key,
            status="sent",
            provider_message_id=provider_message_id,
        )

    async def cancel(self, *, key: str, reason: str) -> None:
        self.cancelled.append(f"{key}:{reason}")

    async def mark_ambiguous(
        self, *, key: str, error: str = "reply_send_failed"
    ) -> None:
        entry = self.entries[key]
        self.entries[key] = FakeOutboxEntry(
            key=entry.key,
            ticket_id=entry.ticket_id,
            generation=entry.generation,
            cursor=entry.cursor,
            locale=entry.locale,
            body=entry.body,
            idempotency_key=entry.idempotency_key,
            status="ambiguous",
            provider_message_id=entry.provider_message_id,
        )


@dataclass
class FakeSender:
    sent: list[tuple[int, str, str]] = field(default_factory=_new_sent)
    before_send: Callable[[], Awaitable[None]] | None = None

    async def send_reply(
        self, *, ticket_id: int, body: str, idempotency_key: str
    ) -> ZendeskReplyReceipt | None:
        if self.before_send is not None:
            callback = self.before_send
            self.before_send = None
            await callback()
        self.sent.append((ticket_id, body, idempotency_key))
        return None


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


def _frozen_config(
    revision: str,
    *,
    reply_enabled: bool = True,
    footer: str = "Configured footer",
) -> FrozenDeliveryConfig:
    return FrozenDeliveryConfig(
        revision=revision,
        reply_enabled=reply_enabled,
        default_locale="en-US",
        footers={"en-US": footer, "ja-JP": footer},
    )


def test_delivery_generation_starts_at_one() -> None:
    with pytest.raises(ValueError, match="generation"):
        _request(generation=0)


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
        policy=DeliveryPolicy(mode=DeliveryMode.LIVE),
    )
    request = _request()

    first = await controller.deliver(request)
    second = await controller.deliver(request)

    assert first.disposition is DeliveryDisposition.SENT
    assert second.disposition is DeliveryDisposition.DEDUPLICATED
    assert second.provider_message_id is None
    assert len(sender.sent) == 1
    assert sender.sent[0][1].count(CANONICAL_FOOTER_MARKER) == 1
    assert sender.sent[0][1].endswith(
        "この返信は Dify AI Agent によって送信されました。"
    )


@pytest.mark.asyncio
async def test_dry_run_renders_reply_without_outbox_or_zendesk_write() -> None:
    freshness = FakeFreshness()
    outbox = FakeOutbox()
    sender = FakeSender()
    controller = DeliveryController(
        freshness=freshness,
        outbox=outbox,
        sender=sender,
        footer_catalog=_catalog(),
        policy=DeliveryPolicy(mode=DeliveryMode.DRY_RUN),
    )

    result = await controller.deliver(_request())

    assert result.disposition is DeliveryDisposition.DRY_RUN
    assert result.reason == "dry_run_no_write"
    assert result.body is not None
    assert result.body.endswith("この返信は Dify AI Agent によって送信されました。")
    assert freshness.reads == 1
    assert outbox.entries == {}
    assert sender.sent == []


@pytest.mark.asyncio
async def test_live_without_sender_fails_closed_before_creating_outbox() -> None:
    outbox = FakeOutbox()
    controller = DeliveryController(
        freshness=FakeFreshness(),
        outbox=outbox,
        sender=UnavailableZendeskReplySender(),
        footer_catalog=_catalog(),
        policy=DeliveryPolicy(mode=DeliveryMode.LIVE),
    )

    result = await controller.deliver(_request())

    assert result.disposition is DeliveryDisposition.FAILED
    assert result.reason == "live_sender_not_ready"
    assert outbox.entries == {}


@pytest.mark.asyncio
async def test_delivery_uses_footer_from_job_frozen_config_revision() -> None:
    configs = FakeDeliveryConfigLoader(
        {
            "cfg-a": _frozen_config("cfg-a", footer="Footer A"),
            "cfg-b": _frozen_config("cfg-b", footer="Footer B"),
        }
    )
    outbox = FakeOutbox()
    controller = DeliveryController(
        freshness=FakeFreshness(),
        outbox=outbox,
        sender=FakeSender(),
        config_loader=configs,
        policy=DeliveryPolicy(mode=DeliveryMode.DRY_RUN),
    )

    first = await controller.deliver(
        DeliveryRequest(
            outcome=_outcome(OutcomeKind.ANSWER, answer="Answer A"),
            generation=7,
            config_revision="cfg-a",
        )
    )
    second = await controller.deliver(
        DeliveryRequest(
            outcome=_outcome(OutcomeKind.ANSWER, answer="Answer B"),
            generation=7,
            config_revision="cfg-b",
        )
    )

    assert first.body is not None and first.body.endswith("Footer A")
    assert second.body is not None and second.body.endswith("Footer B")
    assert configs.revisions == ["cfg-a", "cfg-b"]


@pytest.mark.asyncio
async def test_disabled_frozen_config_does_not_read_or_write_any_delivery_port(
) -> None:
    freshness = FakeFreshness()
    outbox = FakeOutbox()
    sender = FakeSender()
    controller = DeliveryController(
        freshness=freshness,
        outbox=outbox,
        sender=sender,
        config_loader=FakeDeliveryConfigLoader(
            {"cfg-disabled": _frozen_config("cfg-disabled", reply_enabled=False)}
        ),
        policy=DeliveryPolicy(mode=DeliveryMode.LIVE),
    )

    result = await controller.deliver(
        DeliveryRequest(
            outcome=_outcome(OutcomeKind.ANSWER, answer="No write"),
            generation=7,
            config_revision="cfg-disabled",
        )
    )

    assert result.disposition is DeliveryDisposition.DISABLED
    assert result.reason == "reply_delivery_disabled"
    assert freshness.reads == 0
    assert outbox.entries == {}
    assert sender.sent == []


@pytest.mark.asyncio
async def test_canary_without_sender_fails_closed_even_for_allowlisted_ticket() -> None:
    outbox = FakeOutbox()
    controller = DeliveryController(
        freshness=FakeFreshness(),
        outbox=outbox,
        sender=UnavailableZendeskReplySender(),
        footer_catalog=_catalog(),
        policy=DeliveryPolicy(
            mode=DeliveryMode.CANARY,
            canary_ticket_ids=frozenset({3726}),
        ),
    )

    result = await controller.deliver(_request())

    assert result.disposition is DeliveryDisposition.FAILED
    assert result.reason == "canary_sender_not_ready"
    assert outbox.entries == {}


@pytest.mark.asyncio
async def test_delivery_persists_provider_message_id_from_receipt() -> None:
    outbox = FakeOutbox()

    class ReceiptSender(FakeSender):
        async def send_reply(
            self, *, ticket_id: int, body: str, idempotency_key: str
        ) -> ZendeskReplyReceipt:
            self.sent.append((ticket_id, body, idempotency_key))
            return ZendeskReplyReceipt(provider_message_id="comment-3726")

    sender = ReceiptSender()
    controller = DeliveryController(
        freshness=FakeFreshness(),
        outbox=outbox,
        sender=sender,
        footer_catalog=_catalog(),
        policy=DeliveryPolicy(mode=DeliveryMode.LIVE),
    )

    result = await controller.deliver(_request())
    duplicate = await controller.deliver(_request())

    assert result.disposition is DeliveryDisposition.SENT
    assert result.provider_message_id == "comment-3726"
    assert duplicate.disposition is DeliveryDisposition.DEDUPLICATED
    assert duplicate.provider_message_id == "comment-3726"
    entry = outbox.entries[result.outbox_key or ""]
    assert entry.provider_message_id == "comment-3726"


@pytest.mark.asyncio
async def test_provider_timeout_leaves_outbox_ambiguous_for_reconciliation() -> None:
    outbox = FakeOutbox()

    class TimeoutSender(FakeSender):
        async def send_reply(
            self, *, ticket_id: int, body: str, idempotency_key: str
        ) -> None:
            raise TimeoutError("provider timeout after accept")

    controller = DeliveryController(
        freshness=FakeFreshness(),
        outbox=outbox,
        sender=TimeoutSender(),
        footer_catalog=_catalog(),
        policy=DeliveryPolicy(mode=DeliveryMode.LIVE),
    )

    result = await controller.deliver(_request())

    assert result.disposition is DeliveryDisposition.FAILED
    assert result.reason == "reply_send_failed"
    assert outbox.entries[result.outbox_key or ""].status == "ambiguous"


@pytest.mark.asyncio
async def test_canary_mode_allows_only_explicit_ticket_ids() -> None:
    blocked_outbox = FakeOutbox()
    blocked_sender = FakeSender()
    blocked = DeliveryController(
        freshness=FakeFreshness(),
        outbox=blocked_outbox,
        sender=blocked_sender,
        footer_catalog=_catalog(),
        policy=DeliveryPolicy(
            mode=DeliveryMode.CANARY,
            canary_ticket_ids=frozenset({4000}),
        ),
    )

    blocked_result = await blocked.deliver(_request())

    assert blocked_result.disposition is DeliveryDisposition.NOT_ELIGIBLE
    assert blocked_result.reason == "ticket_not_in_canary_allowlist"
    assert blocked_outbox.entries == {}
    assert blocked_sender.sent == []

    allowed_sender = FakeSender()
    allowed = DeliveryController(
        freshness=FakeFreshness(),
        outbox=FakeOutbox(),
        sender=allowed_sender,
        footer_catalog=_catalog(),
        policy=DeliveryPolicy(
            mode=DeliveryMode.CANARY,
            canary_ticket_ids=frozenset({3726}),
        ),
    )

    allowed_result = await allowed.deliver(_request())

    assert allowed_result.disposition is DeliveryDisposition.DRY_RUN
    assert allowed_result.reason == "canary_no_write"
    assert allowed_sender.sent == []


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
        policy=DeliveryPolicy(mode=DeliveryMode.LIVE),
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
        policy=DeliveryPolicy(mode=DeliveryMode.LIVE),
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
        policy=DeliveryPolicy(mode=DeliveryMode.LIVE),
    )

    results = await asyncio.gather(
        controller.deliver(_request()), controller.deliver(_request())
    )

    assert sorted(result.disposition for result in results) == [
        DeliveryDisposition.DEDUPLICATED,
        DeliveryDisposition.SENT,
    ]
    assert len(sender.sent) == 1
