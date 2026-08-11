from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

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
    CaseworkExecution,
    CaseworkRun,
    DispositionDecision,
    FormIdentity,
    IdentityResolution,
    InvestigationResult,
    ReplyDraft,
    RetryDirective,
    TicketSnapshot,
)
from zd_agent.runtime.casework_v2 import apply_casework_execution_to_session
from zd_agent.session.agent_session import AgentSession, SessionStatus


NOW = datetime(2026, 8, 11, 14, 0, tzinfo=timezone.utc)


def _execution(*, retry: bool = False) -> CaseworkExecution:
    context = CaseContext(
        ticket=TicketSnapshot(
            ticket_id=4001,
            generation=5,
            status="open",
            latest_event_id="event-5",
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
    if retry:
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
        reply = None
        receipt = CaseworkEffectReceipt(
            delivery_state=DeliveryState.NOT_REQUIRED,
            ownership_state=OwnershipState.NOT_REQUIRED,
        )
    else:
        decision = DispositionDecision(
            disposition=Disposition.ENGINEERING_ESCALATION,
            reply_kind=ReplyKind.ANSWER,
            ownership_action=OwnershipAction.ENGINEERING,
            reason_code=ReasonCode.CONFIRMED_PLATFORM_DEFECT,
            evidence_ids=("ev-boundary", "ev-cause"),
        )
        reply = ReplyDraft(
            body="We confirmed the issue and queued the required correction.",
            reply_kind=ReplyKind.ANSWER,
            disposition=Disposition.ENGINEERING_ESCALATION,
            ownership_action=OwnershipAction.ENGINEERING,
            reason_code=ReasonCode.CONFIRMED_PLATFORM_DEFECT,
            mentions_human_followup=True,
            reviewed=True,
        )
        receipt = CaseworkEffectReceipt(
            delivery_state=DeliveryState.CONFIRMED,
            ownership_state=OwnershipState.CONFIRMED,
            delivery_reference="casework-effect:1",
            ownership_reference="casework-effect:1",
            accepted_snapshot_count=6,
            accepted_snapshot_last_id="event-5",
        )
    assessment = CaseAssessment(
        question="Why does this fail?",
        answer_status=AnswerStatus.INSUFFICIENT,
    )
    run = CaseworkRun(
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
        delivery_attempted=not retry,
    )
    return CaseworkExecution(run=run, effect_receipt=receipt)


def test_confirmed_v2_execution_sets_typed_terminal_session_state() -> None:
    session = AgentSession(ticket_id=4001)

    apply_casework_execution_to_session(session, _execution())

    assert session.status is SessionStatus.COMPLETED
    assert session.reply_sent is True
    assert session.casework_disposition == "engineering_escalation"
    assert session.casework_ownership_action == "engineering"
    assert session.casework_reason_code == "confirmed_platform_defect"
    assert session.casework_delivery_state == "confirmed"
    assert session.casework_effect_reference == "casework-effect:1"
    assert session.public_reply_type == ""
    assert session.customer_msgs_snapshot_count == 6


def test_v2_retry_pending_defers_without_customer_reply() -> None:
    session = AgentSession(ticket_id=4001)

    apply_casework_execution_to_session(session, _execution(retry=True))

    assert session.status is SessionStatus.DEFERRED
    assert session.reply_sent is False
    assert session.casework_disposition == "retry_pending"
    assert session.casework_delivery_state == "not_required"


async def test_casework_mode_runner_never_enters_legacy_tool_loop(monkeypatch) -> None:
    from zd_agent.runtime import runner

    cfg = MagicMock()
    cfg.agent.guardrails = MagicMock()
    cfg.llm.model = "test-model"
    cfg.casework.mode = ExecutionMode.CASEWORK
    monkeypatch.setattr(runner, "get_config", lambda: cfg)
    monkeypatch.setattr(runner, "_capture_customer_message_snapshot", AsyncMock())
    casework = AsyncMock()
    legacy = AsyncMock()
    monkeypatch.setattr(runner, "_run_casework_agent", casework)
    monkeypatch.setattr(runner, "run_tool_loop", legacy)
    monkeypatch.setattr(
        runner,
        "Guardrails",
        lambda config: MagicMock(elapsed_seconds=0),
    )
    session = AgentSession(ticket_id=4001, dry_run=True)

    await runner.run_agent(session)

    casework.assert_awaited_once_with(session, cfg)
    legacy.assert_not_awaited()
