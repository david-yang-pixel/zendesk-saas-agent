from __future__ import annotations

from datetime import datetime, timezone

from zd_agent.casework.application.execute_case import CaseworkExecutor
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
    CaseworkEffectReceipt,
    CaseworkRun,
    DispositionDecision,
    EffectAuthorityProjection,
    FormIdentity,
    IdentityResolution,
    InvestigationResult,
    ReplyDraft,
    RetryDirective,
    TicketSnapshot,
)


NOW = datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc)


def _context() -> CaseContext:
    return CaseContext(
        ticket=TicketSnapshot(
            ticket_id=4001,
            generation=3,
            status="open",
            latest_event_id="event-3",
            latest_event_at=NOW,
            questions=("What should I do next?",),
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
    mode: ExecutionMode,
    decision: DispositionDecision,
    reply: ReplyDraft | None,
) -> CaseworkRun:
    context = _context()
    assessment = CaseAssessment(
        question="What should I do next?",
        answer_status=AnswerStatus.INSUFFICIENT,
        customer_only_fields=("target_workspace_identity",),
    )
    return CaseworkRun(
        execution_mode=mode,
        started_at=NOW,
        finished_at=NOW,
        context=context,
        investigation=InvestigationResult(
            proposal=AssessmentProposal(
                question=assessment.question,
                answer_status=assessment.answer_status,
            ),
        ),
        assessment_build=AssessmentBuild(assessment=assessment),
        decision=decision,
        reply_draft=reply,
    )


class FakeOrchestrator:
    def __init__(self, run: CaseworkRun) -> None:
        self.run_result = run
        self.modes: list[ExecutionMode] = []

    async def run(self, **kwargs) -> CaseworkRun:
        self.modes.append(kwargs["execution_mode"])
        return self.run_result


class FakeEffects:
    def __init__(self, receipt: CaseworkEffectReceipt) -> None:
        self.receipt = receipt
        self.calls: list[tuple[CaseworkRun, str, bool]] = []

    async def apply(
        self,
        *,
        run: CaseworkRun,
        delivery_attempt_id: str,
        dry_run: bool,
    ) -> CaseworkEffectReceipt:
        self.calls.append((run, delivery_attempt_id, dry_run))
        return self.receipt


def _ask_decision() -> DispositionDecision:
    return DispositionDecision(
        disposition=Disposition.AWAITING_CUSTOMER,
        reply_kind=ReplyKind.ASK,
        ownership_action=OwnershipAction.NONE,
        reason_code=ReasonCode.CUSTOMER_INPUT_REQUIRED,
        required_customer_fields=("target_workspace_identity",),
    )


def _ask_reply() -> ReplyDraft:
    return ReplyDraft(
        body="Please provide the email associated with the affected workspace.",
        reply_kind=ReplyKind.ASK,
        disposition=Disposition.AWAITING_CUSTOMER,
        ownership_action=OwnershipAction.NONE,
        reason_code=ReasonCode.CUSTOMER_INPUT_REQUIRED,
        requested_customer_fields=("target_workspace_identity",),
        reviewed=True,
    )


async def test_casework_executor_delivers_v2_run_through_effect_port() -> None:
    run = _run(
        mode=ExecutionMode.CASEWORK,
        decision=_ask_decision(),
        reply=_ask_reply(),
    )
    orchestrator = FakeOrchestrator(run)
    effects = FakeEffects(
        CaseworkEffectReceipt(
            delivery_state=DeliveryState.CONFIRMED,
            ownership_state=OwnershipState.NOT_REQUIRED,
            delivery_reference="delivery-1",
        ),
    )
    executor = CaseworkExecutor(orchestrator=orchestrator, effects=effects)

    result = await executor.execute(
        ticket=run.context.ticket,
        billing_required=False,
        execution_mode=ExecutionMode.CASEWORK,
        delivery_attempt_id="attempt-1",
        dry_run=False,
    )

    assert orchestrator.modes == [ExecutionMode.CASEWORK]
    assert len(effects.calls) == 1
    assert effects.calls[0][1:] == ("attempt-1", False)
    assert result.run.delivery_attempted is True
    assert result.effect_receipt.delivery_state is DeliveryState.CONFIRMED


async def test_dry_run_invokes_effect_port_without_marking_delivery_attempted() -> None:
    run = _run(
        mode=ExecutionMode.CASEWORK,
        decision=_ask_decision(),
        reply=_ask_reply(),
    )
    orchestrator = FakeOrchestrator(run)
    effects = FakeEffects(
        CaseworkEffectReceipt(
            delivery_state=DeliveryState.DRAFTED,
            ownership_state=OwnershipState.NOT_REQUIRED,
        ),
    )
    executor = CaseworkExecutor(orchestrator=orchestrator, effects=effects)

    result = await executor.execute(
        ticket=run.context.ticket,
        billing_required=False,
        execution_mode=ExecutionMode.CASEWORK,
        delivery_attempt_id="attempt-dry-run",
        dry_run=True,
    )

    assert len(effects.calls) == 1
    assert effects.calls[0][1:] == ("attempt-dry-run", True)
    assert result.run.delivery_attempted is False
    assert result.effect_receipt.delivery_state is DeliveryState.DRAFTED


async def test_executor_projects_frozen_effect_run_before_execution_validation() -> (
    None
):
    regenerated = _run(
        mode=ExecutionMode.CASEWORK,
        decision=_ask_decision(),
        reply=_ask_reply(),
    )
    frozen_decision = DispositionDecision(
        disposition=Disposition.RESOLVED,
        reply_kind=ReplyKind.ANSWER,
        ownership_action=OwnershipAction.NONE,
        reason_code=ReasonCode.RESOLVED_WITH_EVIDENCE,
    )
    frozen_reply = ReplyDraft(
        body="The already-sent frozen answer.",
        reply_kind=ReplyKind.ANSWER,
        disposition=Disposition.RESOLVED,
        ownership_action=OwnershipAction.NONE,
        reason_code=ReasonCode.RESOLVED_WITH_EVIDENCE,
        reviewed=True,
    )
    frozen = _run(
        mode=ExecutionMode.CASEWORK,
        decision=frozen_decision,
        reply=frozen_reply,
    )
    authority = EffectAuthorityProjection.from_run(
        frozen,
        snapshot_customer_message_count=3,
        snapshot_customer_last_message_id="event-3",
    )
    effects = FakeEffects(
        CaseworkEffectReceipt(
            delivery_state=DeliveryState.CONFIRMED,
            ownership_state=OwnershipState.NOT_REQUIRED,
            delivery_reference="frozen-delivery",
            authority_projection=authority,
        )
    )
    executor = CaseworkExecutor(
        orchestrator=FakeOrchestrator(regenerated),
        effects=effects,
    )

    result = await executor.execute(
        ticket=regenerated.context.ticket,
        billing_required=False,
        execution_mode=ExecutionMode.CASEWORK,
        delivery_attempt_id="stable-attempt",
        dry_run=False,
    )

    assert result.run.decision == frozen_decision
    assert result.run.reply_draft == frozen_reply
    assert result.run.delivery_attempted is True
    assert result.effect_receipt.authority_projection is None


async def test_internal_retry_does_not_invoke_delivery_or_ownership() -> None:
    decision = DispositionDecision(
        disposition=Disposition.RETRY_PENDING,
        reply_kind=ReplyKind.NONE,
        reason_code=ReasonCode.INTERNAL_RETRY_PENDING,
        retry_directive=RetryDirective(
            operation="identity.lookup",
            attempt=1,
            max_attempts=3,
            retry_after_seconds=2,
        ),
    )
    run = _run(mode=ExecutionMode.CASEWORK, decision=decision, reply=None)
    orchestrator = FakeOrchestrator(run)
    effects = FakeEffects(
        CaseworkEffectReceipt(
            delivery_state=DeliveryState.CONFIRMED,
            ownership_state=OwnershipState.NOT_REQUIRED,
            delivery_reference="unused-delivery",
        ),
    )
    executor = CaseworkExecutor(orchestrator=orchestrator, effects=effects)

    result = await executor.execute(
        ticket=run.context.ticket,
        billing_required=False,
        execution_mode=ExecutionMode.CASEWORK,
        delivery_attempt_id="attempt-retry",
        dry_run=False,
    )

    assert effects.calls == []
    assert result.effect_receipt.delivery_state is DeliveryState.NOT_REQUIRED
    assert result.effect_receipt.ownership_state is OwnershipState.NOT_REQUIRED
