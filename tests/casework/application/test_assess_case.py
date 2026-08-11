from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from zd_agent.casework.application.assess_case import build_case_assessment
from zd_agent.casework.domain.enums import (
    ActionRequirement,
    AdapterErrorCode,
    AnswerStatus,
    BillingBindingStatus,
    ClaimRejectionCode,
    ClaimRole,
    ClaimStatus,
    Disposition,
    EvidenceAuthority,
    EvidenceResultStatus,
    OwnershipAction,
    ReasonCode,
    ResolutionMethod,
    ResolutionStatus,
)
from zd_agent.casework.domain.models import (
    ActionProposal,
    AssessmentProposal,
    BillingBinding,
    BillingSnapshot,
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


def test_claim_for_another_tenant_is_rejected_even_when_evidence_matches_it() -> None:
    other_target = TargetReference(kind="tenant_id", value=TENANT_2)
    wrong_claim = _claim().model_copy(update={"targets": (other_target,)})
    wrong_evidence = _evidence().model_copy(update={"targets": (other_target,)})

    result = build_case_assessment(
        context=_context(_resolved_identity()),
        proposal=AssessmentProposal(
            question="Is the workspace active?",
            answer_status=AnswerStatus.SUPPORTED,
            proposed_claims=(wrong_claim,),
        ),
        evidence=(wrong_evidence,),
        as_of=NOW,
    )

    assert result.assessment.answer_status is AnswerStatus.INSUFFICIENT
    assert result.admissions[0].rejection_code is ClaimRejectionCode.TARGET_MISMATCH


def test_billing_claim_requires_identity_and_billing_evidence() -> None:
    identity = _resolved_identity()
    snapshot = BillingSnapshot(
        tenant_id=TENANT_1,
        identity_tenant_id=TENANT_1,
        plan="professional",
        subscription_status="active",
        observed_at=NOW,
        evidence_ids=("ev-identity", "ev-billing"),
    )
    context = CaseContext(
        ticket=_ticket(),
        built_at=NOW,
        identity=identity,
        billing_required=True,
        billing=BillingBinding(
            status=BillingBindingStatus.RESOLVED,
            snapshot=snapshot,
            evidence_ids=snapshot.evidence_ids,
        ),
    )
    identity_evidence = _evidence().model_copy(
        update={"evidence_id": "ev-identity", "call_id": "ev-identity"},
    )
    billing_evidence = _evidence().model_copy(
        update={"evidence_id": "ev-billing", "call_id": "ev-billing"},
    )
    billing_claim = _claim("ev-billing")

    missing_identity = build_case_assessment(
        context=context,
        proposal=AssessmentProposal(
            question="What plan is active?",
            answer_status=AnswerStatus.SUPPORTED,
            proposed_claims=(billing_claim,),
        ),
        evidence=(identity_evidence, billing_evidence),
        as_of=NOW,
    )
    complete_claim = billing_claim.model_copy(
        update={"supporting_evidence_ids": ("ev-identity", "ev-billing")},
    )
    complete = build_case_assessment(
        context=context,
        proposal=AssessmentProposal(
            question="What plan is active?",
            answer_status=AnswerStatus.SUPPORTED,
            proposed_claims=(complete_claim,),
        ),
        evidence=(identity_evidence, billing_evidence),
        as_of=NOW,
    )

    assert missing_identity.admissions[0].rejection_code is ClaimRejectionCode.MISSING_EVIDENCE
    assert complete.admissions[0].accepted is True


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


def test_model_cannot_invent_an_unapproved_customer_question() -> None:
    result = build_case_assessment(
        context=_context(_resolved_identity()),
        proposal=AssessmentProposal(
            question="Why did this fail?",
            answer_status=AnswerStatus.INSUFFICIENT,
            proposed_customer_fields=("database_password",),
        ),
        evidence=(),
        as_of=NOW,
    )

    assert result.assessment.customer_only_fields == ()
    assert result.rejected_customer_fields == ("database_password",)


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


def test_model_proposal_cannot_choose_ownership_directly() -> None:
    with pytest.raises(ValidationError, match="human_action"):
        AssessmentProposal.model_validate(
            {
                "question": "Please approve a refund.",
                "answer_status": "insufficient",
                "human_action": {
                    "ownership_action": "billing",
                    "reason_code": "business_approval_required",
                    "summary": "Refund approval remains.",
                    "customer_impact": "The customer is waiting.",
                    "completed_checks": ["Checked request"],
                    "unresolved_action": "Approve it.",
                    "acceptance_criteria": ["Decision recorded"],
                    "evidence_ids": ["ev-current"],
                },
            },
        )


def _action_proposal(requirement: ActionRequirement) -> ActionProposal:
    return ActionProposal(
        requirement=requirement,
        summary="A concrete internal action remains.",
        customer_impact="The customer path remains blocked.",
        completed_checks=("Checked current evidence",),
        unresolved_action="Complete the authorized action.",
        acceptance_criteria=("Customer path is verified",),
        evidence_ids=("ev-current",),
    )


def test_unproven_defect_action_is_rejected_instead_of_transferring_ownership() -> None:
    result = build_case_assessment(
        context=_context(_resolved_identity()),
        proposal=AssessmentProposal(
            question="Why did this fail?",
            answer_status=AnswerStatus.SUPPORTED,
            proposed_claims=(_claim(),),
            action_proposal=_action_proposal(ActionRequirement.CONFIRMED_PLATFORM_DEFECT),
        ),
        evidence=(_evidence(),),
        as_of=NOW,
    )

    assert result.assessment.human_action is None
    assert result.rejected_action_reason == "confirmed defect requires admitted boundary and root-cause claims"
    assert decide_disposition(result.assessment).ownership_action is OwnershipAction.NONE


def test_confirmed_defect_action_maps_to_engineering_in_code() -> None:
    boundary = Claim(
        claim_id="claim-boundary",
        statement="The current request fails at the retrieval boundary.",
        role=ClaimRole.FAILING_BOUNDARY,
        status=ClaimStatus.CONFIRMED,
        supporting_evidence_ids=("ev-boundary",),
        targets=(TARGET,),
        public_allowed=True,
    )
    cause = Claim(
        claim_id="claim-cause",
        statement="The collection binding points to a missing collection.",
        role=ClaimRole.ROOT_CAUSE,
        status=ClaimStatus.CONFIRMED,
        supporting_evidence_ids=("ev-boundary", "ev-cause"),
        targets=(TARGET,),
        public_allowed=True,
    )
    evidence = (
        EvidenceItem(
            evidence_id="ev-boundary",
            call_id="call-boundary",
            source="runtime",
            authority=EvidenceAuthority.CURRENT_AUTHORITATIVE,
            result_status=EvidenceResultStatus.SUCCESS,
            targets=(TARGET,),
            observed_at=NOW,
            supports_roles=(ClaimRole.FAILING_BOUNDARY,),
            payload={"failed": True},
        ),
        EvidenceItem(
            evidence_id="ev-cause",
            call_id="call-cause",
            source="source",
            authority=EvidenceAuthority.CURRENT_SUPPORTING,
            result_status=EvidenceResultStatus.SUCCESS,
            targets=(TARGET,),
            observed_at=NOW,
            supports_roles=(ClaimRole.ROOT_CAUSE,),
            payload={"mechanism": "missing collection binding"},
        ),
    )
    action = _action_proposal(ActionRequirement.CONFIRMED_PLATFORM_DEFECT).model_copy(
        update={"evidence_ids": ("ev-boundary", "ev-cause")},
    )

    result = build_case_assessment(
        context=_context(_resolved_identity()),
        proposal=AssessmentProposal(
            question="Why did retrieval fail?",
            answer_status=AnswerStatus.SUPPORTED,
            proposed_claims=(boundary, cause),
            action_proposal=action,
        ),
        evidence=evidence,
        as_of=NOW,
    )
    decision = decide_disposition(result.assessment)

    assert result.rejected_action_reason == ""
    assert result.assessment.human_action is not None
    assert decision.ownership_action is OwnershipAction.ENGINEERING
    assert decision.reason_code is ReasonCode.CONFIRMED_PLATFORM_DEFECT


def test_explicit_refund_request_with_bound_billing_maps_to_billing() -> None:
    identity = _resolved_identity()
    snapshot = BillingSnapshot(
        tenant_id=TENANT_1,
        identity_tenant_id=TENANT_1,
        plan="professional",
        subscription_status="active",
        observed_at=NOW,
        evidence_ids=("ev-identity", "ev-billing"),
    )
    context = CaseContext(
        ticket=_ticket().model_copy(
            update={"questions": ("Please approve a refund for this renewal.",)},
        ),
        built_at=NOW,
        identity=identity,
        billing_required=True,
        billing=BillingBinding(
            status=BillingBindingStatus.RESOLVED,
            snapshot=snapshot,
            evidence_ids=snapshot.evidence_ids,
        ),
    )
    action = _action_proposal(ActionRequirement.BUSINESS_APPROVAL).model_copy(
        update={"evidence_ids": ("ev-billing",)},
    )

    result = build_case_assessment(
        context=context,
        proposal=AssessmentProposal(
            question="Please approve a refund for this renewal.",
            answer_status=AnswerStatus.INSUFFICIENT,
            action_proposal=action,
        ),
        evidence=(),
        as_of=NOW,
    )
    decision = decide_disposition(result.assessment)

    assert decision.ownership_action is OwnershipAction.BILLING
    assert decision.reason_code is ReasonCode.BUSINESS_APPROVAL_REQUIRED
