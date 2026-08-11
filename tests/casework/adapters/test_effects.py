from __future__ import annotations

from datetime import datetime, timezone
import json
import sqlite3
from typing import cast
from unittest.mock import AsyncMock

import pytest

from zd_agent.config import AppConfig, PublicReplyConfig
from zd_agent.casework.adapters.effects import ZendeskCaseworkEffects
from zd_agent.casework.domain.enums import (
    AnswerStatus,
    DeliveryState,
    Disposition,
    ExecutionMode,
    OwnershipAction,
    OwnershipState,
    ReasonCode,
    ReplyKind,
    ResolutionStatus,
)
from zd_agent.casework.domain.models import (
    AssessmentBuild,
    AssessmentProposal,
    CaseAssessment,
    CaseContext,
    CaseworkExecution,
    CaseworkRun,
    DispositionDecision,
    EffectAuthorityProjection,
    FormIdentity,
    HumanActionRequest,
    IdentityResolution,
    InvestigationResult,
    ReplyDraft,
    TicketSnapshot,
)
from zd_agent.services.casework_effect_store import (
    CaseworkEffectStore,
    PendingCaseworkEffect,
    casework_effect_reference,
)
from zd_agent.services.public_reply_delivery_store import PublicReplyDeliveryStore
from zd_agent.services.reliable_public_reply import ReliablePublicReplyResult
from zd_agent.services.reliable_public_reply import (
    send_casework_public_reply_with_freshness,
)
from zd_agent.tools.public_reply_format import _append_footer
from zd_agent.runtime.pre_send_check import (
    FreshnessResult,
)


NOW = datetime(2026, 8, 11, 13, 0, tzinfo=timezone.utc)


def _context(
    *,
    generation: int = 4,
    latest_event_id: str = "event-4",
) -> CaseContext:
    return CaseContext(
        ticket=TicketSnapshot(
            ticket_id=4001,
            generation=generation,
            status="open",
            latest_event_id=latest_event_id,
            latest_event_at=NOW,
            questions=("Why does this fail?",),
            form_identity=FormIdentity(email="owner@example.test"),
        ),
        built_at=NOW,
        identity=IdentityResolution(
            contact_email="owner@example.test",
            target_identity="owner@example.test",
            status=ResolutionStatus.NO_MEMBERSHIP,
        ),
    )


def _run(
    *,
    ownership: bool,
    reply_body: str = "We confirmed the failing boundary and the remaining correction.",
    generation: int = 4,
    latest_event_id: str = "event-4",
) -> CaseworkRun:
    action = None
    disposition = Disposition.RESOLVED
    owner = OwnershipAction.NONE
    reason = ReasonCode.RESOLVED_WITH_EVIDENCE
    mentions_human = False
    if ownership:
        owner = OwnershipAction.ENGINEERING
        reason = ReasonCode.CONFIRMED_PLATFORM_DEFECT
        disposition = Disposition.ENGINEERING_ESCALATION
        mentions_human = True
        action = HumanActionRequest(
            ownership_action=owner,
            reason_code=reason,
            summary="A confirmed platform defect affects this request.",
            customer_impact="The request cannot complete.",
            completed_checks=("Verified the failing boundary.",),
            unresolved_action="Implement and release the correction.",
            acceptance_criteria=("The same request completes successfully.",),
            evidence_ids=("ev-boundary", "ev-cause"),
        )
    assessment = CaseAssessment(
        question="Why does this fail?",
        answer_status=AnswerStatus.INSUFFICIENT,
        admitted_claims=(),
        human_action=action,
    )
    decision = DispositionDecision(
        disposition=disposition,
        reply_kind=ReplyKind.ANSWER,
        ownership_action=owner,
        reason_code=reason,
        evidence_ids=("ev-boundary", "ev-cause") if ownership else (),
    )
    reply = ReplyDraft(
        body=(f"{reply_body}\n\nThis reply was sent by Dify AI Support."),
        reply_kind=ReplyKind.ANSWER,
        disposition=disposition,
        ownership_action=owner,
        reason_code=reason,
        mentions_human_followup=mentions_human,
        reviewed=True,
    )
    context = _context(
        generation=generation,
        latest_event_id=latest_event_id,
    )
    return CaseworkRun(
        execution_mode=ExecutionMode.CASEWORK,
        started_at=NOW,
        finished_at=NOW,
        context=context,
        investigation=InvestigationResult(
            proposal=AssessmentProposal(
                question="Why does this fail?",
                answer_status=AnswerStatus.INSUFFICIENT,
            ),
        ),
        assessment_build=AssessmentBuild(assessment=assessment),
        decision=decision,
        reply_draft=reply,
    )


class FakeStore:
    def __init__(self) -> None:
        self.prepared = False
        self.public_confirmed = False
        self.kwargs = {}
        self.discarded = False

    def prepare(self, **kwargs) -> PendingCaseworkEffect:
        self.prepared = True
        self.kwargs = kwargs
        return PendingCaseworkEffect(
            reference="casework-effect:1",
            ticket_id=kwargs["ticket_id"],
            delivery_attempt_id=kwargs["delivery_attempt_id"],
            generation=kwargs["generation"],
            source_event_id=kwargs["source_event_id"],
            reply_type=kwargs["reply_type"],
            body=kwargs["body"],
            internal_analysis=kwargs["internal_analysis"],
            snapshot_customer_message_count=kwargs["snapshot_customer_message_count"],
            snapshot_customer_last_message_id=kwargs[
                "snapshot_customer_last_message_id"
            ],
            ownership_action=kwargs["ownership_action"],
            reason_code=kwargs["reason_code"],
            ownership_packet=kwargs["ownership_packet"],
            public_delivery_confirmed=False,
            notification_sent=False,
            notification_ambiguous=False,
            zendesk_tag_sent=False,
            delivery_required=kwargs.get("delivery_required", True),
        )

    def confirm_public_delivery(self, reference: str) -> bool:
        assert reference == "casework-effect:1"
        self.public_confirmed = True
        return True

    def discard_definitely_unsent(self, reference: str) -> bool:
        assert reference == "casework-effect:1"
        self.discarded = True
        return True


async def test_effects_persist_typed_ownership_before_plain_solution_delivery() -> None:
    store = FakeStore()
    send_calls = []
    ownership_calls = []

    async def sender(**kwargs) -> ReliablePublicReplyResult:
        assert store.prepared is True
        send_calls.append(kwargs)
        return ReliablePublicReplyResult(
            True,
            "sent",
            accepted_snapshot_count=5,
            accepted_snapshot_last_id="event-4",
        )

    async def ownership_sender(**kwargs) -> bool:
        assert store.public_confirmed is True
        ownership_calls.append(kwargs)
        return True

    effects = ZendeskCaseworkEffects(
        store=store,
        public_reply_sender=sender,
        ownership_sender=ownership_sender,
        snapshot_customer_message_count=5,
        snapshot_customer_last_message_id="event-4",
    )

    receipt = await effects.apply(
        run=_run(ownership=True),
        delivery_attempt_id="attempt-1",
        dry_run=False,
    )

    assert store.kwargs["ownership_action"] == "engineering"
    assert store.kwargs["reason_code"] == "confirmed_platform_defect"
    assert send_calls[0]["reply_type"] == "solution"
    assert send_calls[0]["body"].endswith("remaining correction.")
    assert "handoff" not in str(send_calls[0]).casefold()
    assert len(ownership_calls) == 1
    assert receipt.delivery_state is DeliveryState.CONFIRMED
    assert receipt.ownership_state is OwnershipState.CONFIRMED


async def test_no_ownership_decision_never_runs_ownership_sender() -> None:
    store = FakeStore()
    ownership_calls = []

    async def sender(**kwargs) -> ReliablePublicReplyResult:
        return ReliablePublicReplyResult(True, "sent")

    async def ownership_sender(**kwargs) -> bool:
        ownership_calls.append(kwargs)
        return True

    effects = ZendeskCaseworkEffects(
        store=store,
        public_reply_sender=sender,
        ownership_sender=ownership_sender,
        snapshot_customer_message_count=1,
        snapshot_customer_last_message_id="event-4",
    )

    receipt = await effects.apply(
        run=_run(ownership=False),
        delivery_attempt_id="attempt-2",
        dry_run=False,
    )

    assert store.kwargs["ownership_action"] == "none"
    assert ownership_calls == []
    assert receipt.ownership_state is OwnershipState.NOT_REQUIRED


async def test_freshness_failure_defers_without_running_ownership() -> None:
    store = FakeStore()
    ownership_calls = []

    async def sender(**kwargs) -> ReliablePublicReplyResult:
        return ReliablePublicReplyResult(
            False,
            "Pre-relay freshness check abort",
            definitely_unsent=True,
        )

    async def ownership_sender(**kwargs) -> bool:
        ownership_calls.append(kwargs)
        return True

    effects = ZendeskCaseworkEffects(
        store=store,
        public_reply_sender=sender,
        ownership_sender=ownership_sender,
        snapshot_customer_message_count=1,
        snapshot_customer_last_message_id="event-4",
    )

    receipt = await effects.apply(
        run=_run(ownership=True),
        delivery_attempt_id="attempt-3",
        dry_run=False,
    )

    assert store.public_confirmed is False
    assert ownership_calls == []
    assert receipt.delivery_state is DeliveryState.DEFERRED
    assert receipt.ownership_state is OwnershipState.PENDING
    assert receipt.delivery_reference == ""
    assert store.discarded is True


async def test_pending_form_defers_without_running_ownership() -> None:
    store = FakeStore()
    ownership_calls = []

    async def sender(**kwargs) -> ReliablePublicReplyResult:
        return ReliablePublicReplyResult(
            False,
            "Public reply deferred because a blocking customer form is pending.",
        )

    async def ownership_sender(**kwargs) -> bool:
        ownership_calls.append(kwargs)
        return True

    effects = ZendeskCaseworkEffects(
        store=store,
        public_reply_sender=sender,
        ownership_sender=ownership_sender,
        snapshot_customer_message_count=1,
        snapshot_customer_last_message_id="event-4",
    )

    receipt = await effects.apply(
        run=_run(ownership=True),
        delivery_attempt_id="attempt-pending-form",
        dry_run=False,
    )

    assert store.public_confirmed is False
    assert ownership_calls == []
    assert receipt.delivery_state is DeliveryState.DEFERRED
    assert receipt.ownership_state is OwnershipState.PENDING


async def test_kill_switch_pauses_customer_write_but_completes_typed_ownership() -> (
    None
):
    store = FakeStore()
    ownership_calls = []

    async def sender(**_kwargs) -> ReliablePublicReplyResult:
        return ReliablePublicReplyResult(
            False,
            "Public reply is currently disabled (kill switch).",
            paused=True,
        )

    async def ownership_sender(**kwargs) -> bool:
        ownership_calls.append(kwargs)
        return True

    effects = ZendeskCaseworkEffects(
        store=store,
        public_reply_sender=sender,
        ownership_sender=ownership_sender,
        snapshot_customer_message_count=1,
        snapshot_customer_last_message_id="event-4",
        public_reply_enabled=False,
    )

    receipt = await effects.apply(
        run=_run(ownership=True),
        delivery_attempt_id="paused-attempt",
        dry_run=False,
    )

    assert receipt.delivery_state is DeliveryState.PAUSED
    assert receipt.ownership_state is OwnershipState.CONFIRMED
    assert receipt.ownership_reference == "casework-effect:1"
    assert store.prepared is True
    assert store.kwargs["delivery_required"] is False
    assert store.public_confirmed is False
    assert len(ownership_calls) == 1


async def test_kill_switch_without_typed_owner_only_archives_the_draft() -> None:
    store = FakeStore()
    ownership_calls = []

    async def ownership_sender(**kwargs) -> bool:
        ownership_calls.append(kwargs)
        return True

    effects = ZendeskCaseworkEffects(
        store=store,
        ownership_sender=ownership_sender,
        snapshot_customer_message_count=1,
        snapshot_customer_last_message_id="event-4",
        public_reply_enabled=False,
    )

    receipt = await effects.apply(
        run=_run(ownership=False),
        delivery_attempt_id="paused-answer",
        dry_run=False,
    )

    assert receipt.delivery_state is DeliveryState.PAUSED
    assert receipt.ownership_state is OwnershipState.NOT_REQUIRED
    assert store.prepared is False
    assert ownership_calls == []


async def test_definitely_unsent_freshness_retry_uses_regenerated_authority(
    tmp_path,
) -> None:
    store = CaseworkEffectStore(tmp_path / "casework_effects.sqlite3")

    async def stale_sender(**kwargs) -> ReliablePublicReplyResult:
        return ReliablePublicReplyResult(
            False,
            "Pre-relay freshness check abort",
            definitely_unsent=True,
        )

    first_effects = ZendeskCaseworkEffects(
        store=store,
        public_reply_sender=stale_sender,
        snapshot_customer_message_count=5,
        snapshot_customer_last_message_id="event-5",
    )
    first_receipt = await first_effects.apply(
        run=_run(ownership=False, reply_body="First frozen answer."),
        delivery_attempt_id="attempt-freshness-retry",
        dry_run=False,
    )
    assert first_receipt.delivery_state is DeliveryState.DEFERRED
    stale_reference = casework_effect_reference(4001, "attempt-freshness-retry")
    assert store.get(stale_reference) is None

    send_calls = []
    ownership_calls = []

    async def retry_sender(**kwargs) -> ReliablePublicReplyResult:
        send_calls.append(kwargs)
        return ReliablePublicReplyResult(True, "sent")

    async def ownership_sender(**kwargs) -> bool:
        ownership_calls.append(kwargs)
        return True

    retry_effects = ZendeskCaseworkEffects(
        store=store,
        public_reply_sender=retry_sender,
        ownership_sender=ownership_sender,
        snapshot_customer_message_count=6,
        snapshot_customer_last_message_id="event-6",
    )
    changed_run = _run(ownership=True, reply_body="Regenerated engineering answer.")
    retry_receipt = await retry_effects.apply(
        run=changed_run,
        delivery_attempt_id="attempt-freshness-retry",
        dry_run=False,
    )

    assert send_calls[0]["body"] == "Regenerated engineering answer."
    assert send_calls[0]["snapshot_customer_message_count"] == 6
    assert send_calls[0]["snapshot_customer_last_message_id"] == "event-6"
    assert send_calls[0]["current_snapshot_customer_message_count"] == 6
    assert send_calls[0]["current_snapshot_customer_last_message_id"] == "event-6"
    assert len(ownership_calls) == 1
    assert retry_receipt.ownership_state is OwnershipState.CONFIRMED
    assert retry_receipt.authority_projection is not None
    execution = CaseworkExecution(
        run=retry_receipt.authority_projection.project(changed_run),
        effect_receipt=retry_receipt.model_copy(
            update={"authority_projection": None},
        ),
    )
    assert execution.run.decision.ownership_action is OwnershipAction.ENGINEERING
    assert execution.run.reply_draft is not None
    assert execution.run.reply_draft.body.startswith("Regenerated engineering answer.")


async def test_effect_authority_projection_excludes_sensitive_run_payload(
    tmp_path,
) -> None:
    database = tmp_path / "casework_effects.sqlite3"
    store = CaseworkEffectStore(database)

    async def defer_sender(**_kwargs) -> ReliablePublicReplyResult:
        return ReliablePublicReplyResult(False, "Pre-relay freshness check abort")

    effects = ZendeskCaseworkEffects(
        store=store,
        public_reply_sender=defer_sender,
        snapshot_customer_message_count=5,
        snapshot_customer_last_message_id="event-4",
    )
    receipt = await effects.apply(
        run=_run(ownership=True),
        delivery_attempt_id="minimal-authority",
        dry_run=False,
    )
    effect = store.get(receipt.delivery_reference)

    assert effect is not None
    payload = json.loads(effect.authority_projection_json)
    assert set(payload) == {
        "generation",
        "source_event_id",
        "snapshot_customer_message_count",
        "snapshot_customer_last_message_id",
        "ownership_action",
        "decision",
        "reply",
    }
    serialized = effect.authority_projection_json.casefold()
    for sensitive in (
        "owner@example.test",
        "target_identity",
        "billing",
        "visual_observations",
        "investigation",
        "assessment_build",
        "internal_analysis",
    ):
        assert sensitive not in serialized
    with sqlite3.connect(database) as connection:
        columns = {
            str(row[1])
            for row in connection.execute(
                "PRAGMA table_info(casework_effects)",
            ).fetchall()
        }
    assert "authority_projection_json" in columns
    assert "authority_run_json" not in columns


def test_legacy_full_run_authority_migrates_and_is_scrubbed(tmp_path) -> None:
    database = tmp_path / "casework_effects.sqlite3"
    run = _run(ownership=False)
    store = CaseworkEffectStore(database)
    effect = store.prepare(
        ticket_id=4001,
        delivery_attempt_id="legacy-full-run",
        generation=4,
        source_event_id="event-4",
        reply_type="solution",
        body="We confirmed the failing boundary and the remaining correction.",
        internal_analysis="Legacy audit",
        snapshot_customer_message_count=5,
        snapshot_customer_last_message_id="event-4",
        ownership_action="none",
        reason_code="resolved_with_evidence",
        ownership_packet="",
    )
    with sqlite3.connect(database) as connection:
        connection.execute(
            "ALTER TABLE casework_effects ADD COLUMN "
            "authority_run_json TEXT NOT NULL DEFAULT ''",
        )
        connection.execute(
            "UPDATE casework_effects SET authority_run_json = ? WHERE reference = ?",
            (run.model_dump_json(), effect.reference),
        )

    migrated_store = CaseworkEffectStore(database)
    migrated = migrated_store.get(effect.reference)

    assert migrated is not None
    assert migrated.authority_projection_json
    assert "owner@example.test" not in migrated.authority_projection_json
    with sqlite3.connect(database) as connection:
        columns = {
            str(row[1])
            for row in connection.execute(
                "PRAGMA table_info(casework_effects)",
            ).fetchall()
        }
    assert "authority_run_json" not in columns


@pytest.mark.parametrize(
    ("row_field", "row_value"),
    [
        ("generation", 99),
        ("source_event_id", "other-event"),
        ("snapshot_customer_message_count", 99),
        ("snapshot_customer_last_message_id", "other-cursor"),
        ("ownership_action", "engineering"),
        ("reason_code", "bounded_authoritative_answer"),
        ("body", "Different frozen body"),
    ],
)
async def test_authority_projection_must_match_every_frozen_effect_field(
    row_field: str,
    row_value: object,
    tmp_path,
) -> None:
    run = _run(ownership=False)
    projection = EffectAuthorityProjection.from_run(
        run,
        snapshot_customer_message_count=5,
        snapshot_customer_last_message_id="event-4",
    )
    row = {
        "generation": 4,
        "source_event_id": "event-4",
        "snapshot_customer_message_count": 5,
        "snapshot_customer_last_message_id": "event-4",
        "ownership_action": "none",
        "reason_code": "resolved_with_evidence",
        "body": "We confirmed the failing boundary and the remaining correction.",
    }
    row[row_field] = row_value
    if row_field == "ownership_action":
        row["reason_code"] = "confirmed_platform_defect"
        row["ownership_packet"] = "Verified engineering action"
    store = CaseworkEffectStore(tmp_path / f"{row_field}.sqlite3")
    store.prepare(
        ticket_id=4001,
        delivery_attempt_id="projection-row-mismatch",
        generation=int(row["generation"]),
        source_event_id=str(row["source_event_id"]),
        reply_type="solution",
        body=str(row["body"]),
        internal_analysis="audit",
        snapshot_customer_message_count=cast(
            int | None,
            row["snapshot_customer_message_count"],
        ),
        snapshot_customer_last_message_id=str(row["snapshot_customer_last_message_id"]),
        ownership_action=str(row["ownership_action"]),
        reason_code=str(row["reason_code"]),
        ownership_packet=str(row.get("ownership_packet", "")),
        authority_projection_json=projection.model_dump_json(),
    )
    sender = AsyncMock(return_value=ReliablePublicReplyResult(True, "sent"))
    effects = ZendeskCaseworkEffects(
        store=store,
        public_reply_sender=sender,
        snapshot_customer_message_count=5,
        snapshot_customer_last_message_id="event-4",
    )

    with pytest.raises(RuntimeError, match="does not match its effect"):
        await effects.apply(
            run=run,
            delivery_attempt_id="projection-row-mismatch",
            dry_run=False,
        )

    sender.assert_not_awaited()


async def test_confirmed_old_snapshot_requeues_before_new_logical_delivery(
    tmp_path,
) -> None:
    """A crash-recovered send must not claim a downtime message was answered."""
    store = CaseworkEffectStore(tmp_path / "casework_effects.sqlite3")
    old_run = _run(
        ownership=False,
        reply_body="Old answer already delivered before the crash.",
        generation=4,
        latest_event_id="event-4",
    )
    old = store.prepare(
        ticket_id=4001,
        delivery_attempt_id="stable-attempt",
        generation=4,
        source_event_id="event-4",
        reply_type="solution",
        body="Old answer already delivered before the crash.",
        internal_analysis="Old audit.",
        snapshot_customer_message_count=4,
        snapshot_customer_last_message_id="event-4",
        ownership_action="none",
        reason_code="resolved_with_evidence",
        ownership_packet="",
        authority_projection_json=EffectAuthorityProjection.from_run(
            old_run,
            snapshot_customer_message_count=4,
            snapshot_customer_last_message_id="event-4",
        ).model_dump_json(),
    )
    send_calls: list[dict] = []

    async def sender(**kwargs) -> ReliablePublicReplyResult:
        send_calls.append(kwargs)
        if len(send_calls) == 1:
            return ReliablePublicReplyResult(
                False,
                "Historical delivery covers event-4, not event-5.",
                accepted_snapshot_count=4,
                accepted_snapshot_last_id="event-4",
                historical_delivery_confirmed=True,
                covers_requested_snapshot=False,
            )
        return ReliablePublicReplyResult(
            True,
            "New logical delivery sent.",
            accepted_snapshot_count=5,
            accepted_snapshot_last_id="event-5",
        )

    effects = ZendeskCaseworkEffects(
        store=store,
        public_reply_sender=sender,
        snapshot_customer_message_count=5,
        snapshot_customer_last_message_id="event-5",
    )
    current_run = _run(
        ownership=False,
        reply_body="Answer generated for the downtime message.",
        generation=5,
        latest_event_id="event-5",
    )

    recovered = await effects.apply(
        run=current_run,
        delivery_attempt_id="stable-attempt",
        dry_run=False,
    )
    assert recovered.delivery_state is DeliveryState.DEFERRED
    confirmed_old = store.get(old.reference)
    assert confirmed_old is not None
    assert confirmed_old.public_delivery_confirmed is True

    delivered = await effects.apply(
        run=current_run,
        delivery_attempt_id="stable-attempt",
        dry_run=False,
    )

    assert delivered.delivery_state is DeliveryState.CONFIRMED
    assert send_calls[0]["body"] == "Old answer already delivered before the crash."
    assert send_calls[0]["snapshot_customer_last_message_id"] == "event-4"
    assert send_calls[1]["body"] == "Answer generated for the downtime message."
    assert send_calls[1]["snapshot_customer_last_message_id"] == "event-5"
    assert send_calls[1]["delivery_attempt_id"] != "stable-attempt"


async def test_legacy_effect_without_authority_rejects_mixed_run_before_send(
    tmp_path,
) -> None:
    store = CaseworkEffectStore(tmp_path / "casework_effects.sqlite3")
    store.prepare(
        ticket_id=4001,
        delivery_attempt_id="legacy-no-authority",
        generation=4,
        source_event_id="event-4",
        reply_type="solution",
        body="Frozen non-ownership answer.",
        internal_analysis="Legacy audit.",
        snapshot_customer_message_count=4,
        snapshot_customer_last_message_id="event-4",
        ownership_action="none",
        reason_code="resolved_with_evidence",
        ownership_packet="",
    )
    sender = AsyncMock(return_value=ReliablePublicReplyResult(True, "sent"))
    effects = ZendeskCaseworkEffects(
        store=store,
        public_reply_sender=sender,
        snapshot_customer_message_count=5,
        snapshot_customer_last_message_id="event-5",
    )

    with pytest.raises(RuntimeError, match="authoritative run projection"):
        await effects.apply(
            run=_run(
                ownership=True,
                reply_body="Regenerated engineering answer.",
            ),
            delivery_attempt_id="legacy-no-authority",
            dry_run=False,
        )

    sender.assert_not_awaited()


async def test_real_cross_store_crash_recovery_promotes_old_then_sends_next(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The production sender must inspect delivered D1 before stale freshness."""
    cfg = AppConfig(
        public_reply=PublicReplyConfig(
            enabled=True,
            browser_relay_url="https://relay.example.test/mcp",
            browser_relay_api_key="relay-secret",
            freshness_callback_url=(
                "https://relay.example.test/webhook/relay-pre-send-freshness"
            ),
        )
    )
    cfg.resources.base_dir = str(tmp_path)
    cfg.resources.state_dir = ".state"
    relay = AsyncMock()
    zendesk = AsyncMock(
        return_value={"success": True, "data": {"comments": []}},
    )

    async def readiness(**_kwargs) -> FreshnessResult:
        return FreshnessResult(continue_send=True, reason="fresh")

    monkeypatch.setattr(
        "zd_agent.services.reliable_public_reply.get_config",
        lambda: cfg,
    )
    monkeypatch.setattr(
        "zd_agent.services.reliable_public_reply.get_messaging_context",
        lambda _ticket_id: {"conversation_id": "conversation-4001"},
    )
    monkeypatch.setattr(
        "zd_agent.services.reliable_public_reply._check_freshness_before_relay",
        readiness,
    )
    monkeypatch.setattr(
        "zd_agent.services.reliable_public_reply.send_browser_relay_reply",
        relay,
    )
    monkeypatch.setattr(
        "zd_agent.services.reliable_public_reply.call_zendesk_tool",
        zendesk,
    )

    store = CaseworkEffectStore(tmp_path / ".state" / "casework_effects.sqlite3")
    old_run = _run(
        ownership=False,
        reply_body="Old answer delivered immediately before the crash.",
        generation=4,
        latest_event_id="event-4",
    )
    old = store.prepare(
        ticket_id=4001,
        delivery_attempt_id="stable-attempt",
        generation=4,
        source_event_id="event-4",
        reply_type="solution",
        body="Old answer delivered immediately before the crash.",
        internal_analysis="Old frozen audit.",
        snapshot_customer_message_count=4,
        snapshot_customer_last_message_id="event-4",
        ownership_action="none",
        reason_code="resolved_with_evidence",
        ownership_packet="",
        authority_projection_json=EffectAuthorityProjection.from_run(
            old_run,
            snapshot_customer_message_count=4,
            snapshot_customer_last_message_id="event-4",
        ).model_dump_json(),
    )
    first = await send_casework_public_reply_with_freshness(
        ticket_id=4001,
        body=old.body,
        reply_type="solution",
        snapshot_customer_message_count=4,
        snapshot_customer_last_message_id="event-4",
        delivery_attempt_id=old.delivery_attempt_id,
        internal_analysis=old.internal_analysis,
    )
    assert first.success is True
    assert store.get(old.reference) == old  # process dies before outer promotion

    current_run = _run(
        ownership=False,
        reply_body="Answer generated for the downtime customer message.",
        generation=5,
        latest_event_id="event-5",
    )
    effects = ZendeskCaseworkEffects(
        store=store,
        snapshot_customer_message_count=5,
        snapshot_customer_last_message_id="event-5",
    )

    recovered = await effects.apply(
        run=current_run,
        delivery_attempt_id="stable-attempt",
        dry_run=False,
    )
    assert recovered.delivery_state is DeliveryState.DEFERRED
    promoted = store.get(old.reference)
    assert promoted is not None and promoted.public_delivery_confirmed

    next_delivery = await effects.apply(
        run=current_run,
        delivery_attempt_id="stable-attempt",
        dry_run=False,
    )

    assert next_delivery.delivery_state is DeliveryState.CONFIRMED
    assert next_delivery.accepted_snapshot_last_id == "event-5"
    assert relay.await_count == 2
    public_bodies = [call.kwargs["body"] for call in relay.await_args_list]
    assert public_bodies[0].startswith("Old answer delivered")
    assert public_bodies[1].startswith("Answer generated for the downtime")


async def test_real_freshness_abort_retires_outer_intent_and_sends_new_cursor(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = AppConfig(
        public_reply=PublicReplyConfig(
            enabled=True,
            browser_relay_url="https://relay.example.test/mcp",
            browser_relay_api_key="relay-secret",
            freshness_callback_url=(
                "https://relay.example.test/webhook/relay-pre-send-freshness"
            ),
        ),
    )
    cfg.resources.base_dir = str(tmp_path)
    cfg.resources.state_dir = ".state"
    relay = AsyncMock()
    zendesk = AsyncMock(return_value={"success": True, "data": {"comments": []}})

    async def readiness(**kwargs) -> FreshnessResult:
        if kwargs["snapshot_last_id"] == "event-5":
            return FreshnessResult(
                continue_send=False,
                reason="substantive_new_message",
                new_messages_count=1,
            )
        return FreshnessResult(continue_send=True, reason="fresh")

    monkeypatch.setattr(
        "zd_agent.services.reliable_public_reply.get_config",
        lambda: cfg,
    )
    monkeypatch.setattr(
        "zd_agent.services.reliable_public_reply.get_messaging_context",
        lambda _ticket_id: {"conversation_id": "conversation-4001"},
    )
    monkeypatch.setattr(
        "zd_agent.services.reliable_public_reply._check_freshness_before_relay",
        readiness,
    )
    monkeypatch.setattr(
        "zd_agent.services.reliable_public_reply.send_browser_relay_reply",
        relay,
    )
    monkeypatch.setattr(
        "zd_agent.services.reliable_public_reply.call_zendesk_tool",
        zendesk,
    )

    store = CaseworkEffectStore(tmp_path / ".state" / "casework_effects.sqlite3")
    stale_effects = ZendeskCaseworkEffects(
        store=store,
        snapshot_customer_message_count=5,
        snapshot_customer_last_message_id="event-5",
    )
    stale = await stale_effects.apply(
        run=_run(
            ownership=False,
            reply_body="Answer generated before the new customer message.",
            generation=5,
            latest_event_id="event-5",
        ),
        delivery_attempt_id="freshness-stable-attempt",
        dry_run=False,
    )

    assert stale.delivery_state is DeliveryState.DEFERRED
    assert (
        store.get(
            casework_effect_reference(4001, "freshness-stable-attempt"),
        )
        is None
    )
    relay.assert_not_awaited()

    current_effects = ZendeskCaseworkEffects(
        store=store,
        snapshot_customer_message_count=6,
        snapshot_customer_last_message_id="event-6",
    )
    current = await current_effects.apply(
        run=_run(
            ownership=False,
            reply_body="Answer generated for the current customer message.",
            generation=6,
            latest_event_id="event-6",
        ),
        delivery_attempt_id="freshness-stable-attempt",
        dry_run=False,
    )

    assert current.delivery_state is DeliveryState.CONFIRMED
    assert current.accepted_snapshot_last_id == "event-6"
    relay.assert_awaited_once()
    assert relay.await_args is not None
    assert relay.await_args.kwargs["body"].startswith(
        "Answer generated for the current customer message.",
    )


async def test_real_cross_store_ambiguous_restart_reconciles_before_freshness(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Remote proof closes D1, then a distinct D2 answers the new cursor."""
    cfg = AppConfig(
        public_reply=PublicReplyConfig(
            enabled=True,
            browser_relay_url="https://relay.example.test/mcp",
            browser_relay_api_key="relay-secret",
            freshness_callback_url=(
                "https://relay.example.test/webhook/relay-pre-send-freshness"
            ),
        )
    )
    cfg.resources.base_dir = str(tmp_path)
    cfg.resources.state_dir = ".state"
    casework_store = CaseworkEffectStore(
        tmp_path / ".state" / "casework_effects.sqlite3"
    )
    old_run = _run(
        ownership=False,
        reply_body="Old answer possibly sent before the crash.",
        generation=4,
        latest_event_id="event-4",
    )
    old = casework_store.prepare(
        ticket_id=4001,
        delivery_attempt_id="ambiguous-stable-attempt",
        generation=4,
        source_event_id="event-4",
        reply_type="solution",
        body="Old answer possibly sent before the crash.",
        internal_analysis="Old frozen audit.",
        snapshot_customer_message_count=4,
        snapshot_customer_last_message_id="event-4",
        ownership_action="none",
        reason_code="resolved_with_evidence",
        ownership_packet="",
        authority_projection_json=EffectAuthorityProjection.from_run(
            old_run,
            snapshot_customer_message_count=4,
            snapshot_customer_last_message_id="event-4",
        ).model_dump_json(),
    )
    public_store = PublicReplyDeliveryStore(
        tmp_path / ".state" / "public_reply_outbox.sqlite3"
    )
    possible = public_store.prepare(
        delivery_attempt_id=old.delivery_attempt_id,
        ticket_id=4001,
        reply_type="solution",
        body=_append_footer(old.body, "solution"),
        internal_analysis=old.internal_analysis,
        snapshot_customer_message_count=4,
        snapshot_customer_last_message_id="event-4",
    )
    token = public_store.claim(possible.delivery_key)
    assert token is not None
    assert public_store.begin_send(possible.delivery_key, token)

    sequence: list[str] = []

    async def zendesk(name: str, arguments: dict) -> dict:
        if name == "get_ticket_comments":
            sequence.append(
                "remote_lookup"
                if arguments.get("include_conversation_log")
                else "note_lookup"
            )
            return {
                "success": True,
                "data": {
                    "comments": [
                        {
                            "public": True,
                            "body": possible.body,
                            "created_at": possible.created_at + 1,
                        }
                    ]
                },
            }
        if name == "add_internal_note":
            return {"success": True, "data": {"comment_id": 9001}}
        raise AssertionError(f"unexpected Zendesk tool: {name}")

    async def readiness(**kwargs) -> FreshnessResult:
        sequence.append(f"freshness:{kwargs['snapshot_last_id']}")
        if kwargs["snapshot_last_id"] == "event-4":
            return FreshnessResult(
                continue_send=False,
                reason="stale_customer_context",
                new_messages_count=1,
            )
        return FreshnessResult(continue_send=True, reason="fresh")

    relay = AsyncMock()
    monkeypatch.setattr(
        "zd_agent.services.reliable_public_reply.get_config",
        lambda: cfg,
    )
    monkeypatch.setattr(
        "zd_agent.services.reliable_public_reply.get_messaging_context",
        lambda _ticket_id: {"conversation_id": "conversation-4001"},
    )
    monkeypatch.setattr(
        "zd_agent.services.reliable_public_reply._check_freshness_before_relay",
        readiness,
    )
    monkeypatch.setattr(
        "zd_agent.services.reliable_public_reply.send_browser_relay_reply",
        relay,
    )
    monkeypatch.setattr(
        "zd_agent.services.reliable_public_reply.call_zendesk_tool",
        zendesk,
    )
    effects = ZendeskCaseworkEffects(
        store=casework_store,
        snapshot_customer_message_count=5,
        snapshot_customer_last_message_id="event-5",
    )
    current_run = _run(
        ownership=False,
        reply_body="Answer for the customer message received during downtime.",
        generation=5,
        latest_event_id="event-5",
    )

    recovered = await effects.apply(
        run=current_run,
        delivery_attempt_id="ambiguous-stable-attempt",
        dry_run=False,
    )
    delivered = await effects.apply(
        run=current_run,
        delivery_attempt_id="ambiguous-stable-attempt",
        dry_run=False,
    )

    assert recovered.delivery_state is DeliveryState.DEFERRED
    assert delivered.delivery_state is DeliveryState.CONFIRMED
    assert sequence[:2] == ["remote_lookup", "freshness:event-4"]
    assert sequence.count("remote_lookup") == 2
    assert relay.await_count == 1
    promoted = casework_store.get(old.reference)
    assert promoted is not None and promoted.public_delivery_confirmed
    reconciled = public_store.get(
        delivery_attempt_id=old.delivery_attempt_id,
        ticket_id=4001,
    )
    assert reconciled is not None and reconciled.delivered


async def test_dry_run_does_not_persist_or_send() -> None:
    store = FakeStore()

    async def fail_sender(**kwargs) -> ReliablePublicReplyResult:
        raise AssertionError("dry run must not call the transport")

    async def fail_ownership(**kwargs) -> bool:
        raise AssertionError("dry run must not call ownership effects")

    effects = ZendeskCaseworkEffects(
        store=store,
        public_reply_sender=fail_sender,
        ownership_sender=fail_ownership,
        snapshot_customer_message_count=1,
        snapshot_customer_last_message_id="event-4",
    )

    receipt = await effects.apply(
        run=_run(ownership=True),
        delivery_attempt_id="attempt-dry",
        dry_run=True,
    )

    assert store.prepared is False
    assert receipt.delivery_state is DeliveryState.DRAFTED
    assert receipt.ownership_state is OwnershipState.PENDING
