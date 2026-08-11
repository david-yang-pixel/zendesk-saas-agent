from datetime import datetime, timezone

from zd_agent.casework.application.assess_case import build_case_assessment
from zd_agent.casework.domain.enums import (
    AdapterErrorCode,
    AnswerStatus,
    ClaimRole,
    ClaimStatus,
    Disposition,
    EvidenceAuthority,
    EvidenceResultStatus,
    ReasonCode,
    ResolutionMethod,
    ResolutionStatus,
)
from zd_agent.casework.domain.models import (
    AssessmentProposal,
    CaseContext,
    Claim,
    EvidenceItem,
    FormIdentity,
    IdentityResolution,
    TargetReference,
    TicketSnapshot,
    WorkspaceCandidate,
)
from zd_agent.casework.policies.disposition import decide_disposition


NOW = datetime(2026, 8, 11, 6, 0, tzinfo=timezone.utc)
TENANT_1 = "11111111-1111-4111-8111-111111111111"
TENANT_2 = "22222222-2222-4222-8222-222222222222"
TARGET = TargetReference(kind="tenant_id", value=TENANT_1)


def _ticket() -> TicketSnapshot:
    return TicketSnapshot(
        ticket_id=4001,
        generation=1,
        status="open",
        channel="messaging",
        latest_event_id="event-1",
        latest_event_at=NOW,
        questions=("What happened?",),
        form_identity=FormIdentity(email="owner@example.com"),
    )


def _resolved_identity() -> IdentityResolution:
    candidate = WorkspaceCandidate(tenant_id=TENANT_1, workspace_name="Acme")
    return IdentityResolution(
        contact_email="owner@example.com",
        target_identity="owner@example.com",
        status=ResolutionStatus.RESOLVED,
        method=ResolutionMethod.UNIQUE_EMAIL_MEMBERSHIP,
        candidates=(candidate,),
        selected_tenant_id=TENANT_1,
        selected_workspace_name="Acme",
        evidence_ids=("ev-identity",),
    )


def _context(identity: IdentityResolution) -> CaseContext:
    return CaseContext(
        ticket=_ticket(),
        built_at=NOW,
        identity=identity,
    )


def _claim(evidence_id: str = "ev-current") -> Claim:
    return Claim(
        claim_id="claim-current",
        statement="The workspace is currently active.",
        role=ClaimRole.CURRENT_STATE,
        status=ClaimStatus.CONFIRMED,
        supporting_evidence_ids=(evidence_id,),
        targets=(TARGET,),
        public_allowed=True,
    )


def _evidence() -> EvidenceItem:
    return EvidenceItem(
        evidence_id="ev-current",
        call_id="call-current",
        source="account-reader",
        authority=EvidenceAuthority.CURRENT_AUTHORITATIVE,
        result_status=EvidenceResultStatus.SUCCESS,
        targets=(TARGET,),
        observed_at=NOW,
        supports_roles=(ClaimRole.CURRENT_STATE,),
        payload={"active": True},
    )


def test_assessment_contains_only_claims_that_pass_evidence_admission() -> None:
    result = build_case_assessment(
        context=_context(_resolved_identity()),
        proposal=AssessmentProposal(
            question="Is the workspace active?",
            answer_status=AnswerStatus.SUPPORTED,
            proposed_claims=(_claim(),),
        ),
        evidence=(_evidence(),),
        as_of=NOW,
    )

    assert result.assessment.answer_status is AnswerStatus.SUPPORTED
    assert result.assessment.admitted_claims == (_claim(),)
    assert result.admissions[0].accepted is True


def test_rejected_material_claim_downgrades_answer_to_insufficient() -> None:
    result = build_case_assessment(
        context=_context(_resolved_identity()),
        proposal=AssessmentProposal(
            question="Is the workspace active?",
            answer_status=AnswerStatus.SUPPORTED,
            proposed_claims=(_claim("missing-evidence"),),
        ),
        evidence=(_evidence(),),
        as_of=NOW,
    )

    assert result.assessment.answer_status is AnswerStatus.INSUFFICIENT
    assert result.assessment.admitted_claims == ()
    assert result.admissions[0].accepted is False


def test_resolved_identity_removes_redundant_workspace_id_request() -> None:
    result = build_case_assessment(
        context=_context(_resolved_identity()),
        proposal=AssessmentProposal(
            question="Which workspace is affected?",
            answer_status=AnswerStatus.INSUFFICIENT,
            proposed_customer_fields=("workspace_id",),
        ),
        evidence=(),
        as_of=NOW,
    )

    assert result.assessment.customer_only_fields == ()
    assert "workspace_id" in result.rejected_customer_fields


def test_failed_identity_lookup_becomes_internal_retry_not_customer_ask() -> None:
    identity = IdentityResolution(
        contact_email="owner@example.com",
        target_identity="owner@example.com",
        status=ResolutionStatus.LOOKUP_FAILED,
        error_code=AdapterErrorCode.TIMEOUT,
        evidence_ids=("ev-timeout",),
        detail="support-side lookup timed out",
    )
    result = build_case_assessment(
        context=_context(identity),
        proposal=AssessmentProposal(
            question="Which workspace is affected?",
            answer_status=AnswerStatus.INSUFFICIENT,
            proposed_customer_fields=("workspace_id",),
        ),
        evidence=(),
        as_of=NOW,
    )
    decision = decide_disposition(result.assessment)

    assert result.assessment.customer_only_fields == ()
    assert result.assessment.blocking_failure is not None
    assert result.assessment.blocking_failure.code is AdapterErrorCode.TIMEOUT
    assert decision.disposition is Disposition.RETRY_PENDING
    assert decision.reason_code is ReasonCode.INTERNAL_RETRY_PENDING


def test_true_identity_ambiguity_allows_only_minimum_workspace_choice() -> None:
    identity = IdentityResolution(
        contact_email="owner@example.com",
        target_identity="owner@example.com",
        status=ResolutionStatus.AMBIGUOUS,
        candidates=(
            WorkspaceCandidate(tenant_id=TENANT_1, workspace_name="One"),
            WorkspaceCandidate(tenant_id=TENANT_2, workspace_name="Two"),
        ),
        evidence_ids=("ev-memberships",),
    )
    result = build_case_assessment(
        context=_context(identity),
        proposal=AssessmentProposal(
            question="Which workspace is affected?",
            answer_status=AnswerStatus.INSUFFICIENT,
            proposed_customer_fields=("workspace_id", "workspace_name"),
        ),
        evidence=(),
        as_of=NOW,
    )
    decision = decide_disposition(result.assessment)

    assert result.assessment.customer_only_fields == ("target_workspace_choice",)
    assert decision.disposition is Disposition.AWAITING_CUSTOMER
    assert decision.required_customer_fields == ("target_workspace_choice",)


def test_pending_identity_lookup_uses_retry_even_without_model_requested_field() -> None:
    identity = IdentityResolution(
        contact_email="owner@example.com",
        target_identity="owner@example.com",
        status=ResolutionStatus.LOOKUP_PENDING,
        evidence_ids=("ev-form",),
    )
    result = build_case_assessment(
        context=_context(identity),
        proposal=AssessmentProposal(
            question="What plan is this workspace using?",
            answer_status=AnswerStatus.INSUFFICIENT,
        ),
        evidence=(),
        as_of=NOW,
    )

    assert result.assessment.blocking_failure is not None
    assert result.assessment.blocking_failure.code is AdapterErrorCode.TRANSPORT_UNAVAILABLE
