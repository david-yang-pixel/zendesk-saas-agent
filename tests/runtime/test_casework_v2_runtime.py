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
from zd_agent.services.casework_runtime_failures import (
    RuntimeFailureOwnershipReceipt,
)
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


async def test_runner_has_only_the_v2_casework_path(monkeypatch) -> None:
    from zd_agent.runtime import runner

    cfg = MagicMock()
    cfg.agent.guardrails = MagicMock()
    cfg.casework.mode = ExecutionMode.CASEWORK
    monkeypatch.setattr(runner, "get_config", lambda: cfg)
    casework = AsyncMock()
    monkeypatch.setattr(runner, "_run_casework_agent", casework)
    session = AgentSession(ticket_id=4001, dry_run=True)

    await runner.run_agent(session)

    casework.assert_awaited_once_with(session, cfg)
    assert not hasattr(runner, "run_tool_loop")
    assert not hasattr(runner, "create_tool_registry")


async def test_v2_runtime_failure_retries_before_creating_ownership(
    monkeypatch,
) -> None:
    from zd_agent.runtime import runner

    persist = AsyncMock()
    monkeypatch.setattr(runner, "persist_runtime_failure_ownership", persist)
    session = AgentSession(ticket_id=4001, casework_attempt=2)
    cfg = MagicMock()

    await runner._handle_casework_runtime_failure(
        session,
        cfg,
        TimeoutError("provider timed out"),
    )

    assert session.status is SessionStatus.FAILED
    assert session.casework_ownership_action == ""
    persist.assert_not_awaited()


async def test_v2_runtime_failure_exhaustion_persists_typed_ownership(
    monkeypatch,
    tmp_path,
) -> None:
    from zd_agent.runtime import runner

    persist = AsyncMock(
        return_value=RuntimeFailureOwnershipReceipt(
            reference="casework-effect:runtime",
            ownership_action=OwnershipAction.SUPPORT,
            reason_code=ReasonCode.AUTOMATION_RETRY_EXHAUSTED,
            ownership_state=OwnershipState.CONFIRMED,
        ),
    )
    monkeypatch.setattr(runner, "persist_runtime_failure_ownership", persist)
    session = AgentSession(
        ticket_id=4001,
        delivery_attempt_id="delivery-1",
        casework_attempt=3,
    )
    session.customer_msgs_snapshot_count = 2
    session.customer_msgs_snapshot_last_id = "event-2"
    cfg = MagicMock()
    cfg.resources.base_dir = str(tmp_path)
    cfg.resources.state_dir = "state"

    await runner._handle_casework_runtime_failure(
        session,
        cfg,
        TimeoutError("provider timed out"),
    )

    assert session.status is SessionStatus.COMPLETED
    assert session.reply_sent is False
    assert session.casework_ownership_action == "support"
    assert session.casework_reason_code == "automation_retry_exhausted"
    assert session.casework_delivery_state == "not_required"
    assert session.casework_effect_reference == "casework-effect:runtime"
    persist.assert_awaited_once()


async def test_ownership_persistence_retry_skips_the_model(
    monkeypatch,
    tmp_path,
) -> None:
    from zd_agent.runtime import runner

    cfg = MagicMock()
    cfg.agent.guardrails = MagicMock()
    cfg.casework.mode.value = "casework"
    cfg.resources.base_dir = str(tmp_path)
    cfg.resources.state_dir = "state"
    session = AgentSession(ticket_id=4001, casework_attempt=4)
    registry = MagicMock()
    llm = MagicMock()
    execute = AsyncMock()
    persist = AsyncMock(
        return_value=RuntimeFailureOwnershipReceipt(
            reference="casework-effect:runtime",
            ownership_action=OwnershipAction.SUPPORT,
            reason_code=ReasonCode.AUTOMATION_RETRY_EXHAUSTED,
            ownership_state=OwnershipState.CONFIRMED,
        ),
    )
    monkeypatch.setattr(runner, "create_casework_tool_registry", registry)
    monkeypatch.setattr(runner, "LLMClient", llm)
    monkeypatch.setattr(runner, "execute_casework_v2", execute)
    monkeypatch.setattr(runner, "persist_runtime_failure_ownership", persist)
    monkeypatch.setattr(runner, "ensure_archive", MagicMock())

    await runner._run_casework_agent(session, cfg)

    registry.assert_not_called()
    llm.assert_not_called()
    execute.assert_not_awaited()
    persist.assert_awaited_once()
    assert session.status is SessionStatus.COMPLETED
    assert session.casework_ownership_action == "support"
