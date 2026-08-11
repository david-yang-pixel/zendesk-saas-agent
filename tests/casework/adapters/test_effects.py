from __future__ import annotations

from datetime import datetime, timezone

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
    CaseworkRun,
    DispositionDecision,
    FormIdentity,
    HumanActionRequest,
    IdentityResolution,
    InvestigationResult,
    ReplyDraft,
    TicketSnapshot,
)
from zd_agent.services.casework_effect_store import PendingCaseworkEffect
from zd_agent.tools.zendesk_public_reply import DirectPublicReplyResult


NOW = datetime(2026, 8, 11, 13, 0, tzinfo=timezone.utc)


def _context() -> CaseContext:
    return CaseContext(
        ticket=TicketSnapshot(
            ticket_id=4001,
            generation=4,
            status="open",
            latest_event_id="event-4",
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


def _run(*, ownership: bool) -> CaseworkRun:
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
        body=(
            "We confirmed the failing boundary and the remaining correction.\n\n"
            "This reply was sent by Dify AI Support."
        ),
        reply_kind=ReplyKind.ANSWER,
        disposition=disposition,
        ownership_action=owner,
        reason_code=reason,
        mentions_human_followup=mentions_human,
        reviewed=True,
    )
    context = _context()
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
            snapshot_customer_last_message_id=kwargs["snapshot_customer_last_message_id"],
            ownership_action=kwargs["ownership_action"],
            reason_code=kwargs["reason_code"],
            ownership_packet=kwargs["ownership_packet"],
            public_delivery_confirmed=False,
            notification_sent=False,
            notification_ambiguous=False,
            zendesk_tag_sent=False,
        )

    def confirm_public_delivery(self, reference: str) -> bool:
        assert reference == "casework-effect:1"
        self.public_confirmed = True
        return True


async def test_effects_persist_typed_ownership_before_plain_solution_delivery() -> None:
    store = FakeStore()
    send_calls = []
    ownership_calls = []

    async def sender(**kwargs) -> DirectPublicReplyResult:
        assert store.prepared is True
        send_calls.append(kwargs)
        return DirectPublicReplyResult(
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

    async def sender(**kwargs) -> DirectPublicReplyResult:
        return DirectPublicReplyResult(True, "sent")

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

    async def sender(**kwargs) -> DirectPublicReplyResult:
        return DirectPublicReplyResult(False, "Pre-relay freshness check abort")

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


async def test_dry_run_does_not_persist_or_send() -> None:
    store = FakeStore()

    async def fail_sender(**kwargs) -> DirectPublicReplyResult:
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
