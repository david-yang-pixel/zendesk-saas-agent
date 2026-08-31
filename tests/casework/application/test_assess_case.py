from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from zd_agent.casework.adapters.evidence import (
    build_case_context_evidence,
    tool_evidence,
)
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
    VisualObservationStatus,
)
from zd_agent.casework.domain.models import (
    ActionProposal,
    AssessmentProposal,
    AttachmentRef,
    BillingBinding,
    BillingSnapshot,
    CaseContext,
    Claim,
    EvidenceItem,
    FormIdentity,
    IdentityResolution,
    TargetReference,
    TicketSnapshot,
    VisualObservation,
    WorkspaceCandidate,
)
from zd_agent.casework.policies.disposition import decide_disposition
from zd_agent.casework.policies.ownership import admit_action_proposal


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


def test_identity_membership_only_cannot_admit_unrelated_current_state() -> None:
    context = _context(_resolved_identity())
    [identity_evidence] = build_case_context_evidence(context)
    claim = _claim("ev-identity").model_copy(
        update={"statement": "The workspace retrieval is currently failing."},
    )

    result = build_case_assessment(
        context=context,
        proposal=AssessmentProposal(
            question="Why is retrieval failing?",
            answer_status=AnswerStatus.SUPPORTED,
            proposed_claims=(claim,),
        ),
        evidence=(identity_evidence,),
        as_of=NOW,
    )

    assert result.assessment.answer_status is AnswerStatus.INSUFFICIENT
    assert result.assessment.admitted_claims == ()
    assert result.admissions[0].rejection_code is ClaimRejectionCode.ROLE_MISMATCH


def _inspector_evidence(query_tenant_id: str) -> EvidenceItem:
    return tool_evidence(
        call_id="ev-inspector",
        tool_name="inspector_exec",
        args={
            "tenant_id": query_tenant_id,
            "section": "quota",
        },
        allowed_target_arguments=frozenset({"tenant_id", "section"}),
        observed_at=NOW,
        success=True,
        output="Workspace quota is active.",
        error="",
    )


def test_target_scoped_inspector_can_admit_honest_tenant_current_state() -> None:
    context = _context(_resolved_identity())
    [identity_evidence] = build_case_context_evidence(context)
    inspector_evidence = _inspector_evidence(TENANT_1)
    claim = _claim("ev-inspector").model_copy(
        update={"supporting_evidence_ids": ("ev-inspector", "ev-identity")},
    )

    result = build_case_assessment(
        context=context,
        proposal=AssessmentProposal(
            question="Is the workspace active?",
            answer_status=AnswerStatus.SUPPORTED,
            proposed_claims=(claim,),
        ),
        evidence=(identity_evidence, inspector_evidence),
        as_of=NOW,
    )

    assert inspector_evidence.target_value("tenant_id") == TENANT_1
    assert result.admissions[0].accepted is True


def test_inspector_for_other_tenant_cannot_support_resolved_tenant() -> None:
    context = _context(_resolved_identity())
    [identity_evidence] = build_case_context_evidence(context)
    inspector_evidence = _inspector_evidence(TENANT_2)
    claim = _claim("ev-inspector").model_copy(
        update={"supporting_evidence_ids": ("ev-inspector", "ev-identity")},
    )

    result = build_case_assessment(
        context=context,
        proposal=AssessmentProposal(
            question="Is the workspace active?",
            answer_status=AnswerStatus.SUPPORTED,
            proposed_claims=(claim,),
        ),
        evidence=(identity_evidence, inspector_evidence),
        as_of=NOW,
    )

    assert inspector_evidence.target_value("tenant_id") == TENANT_2
    assert result.admissions[0].rejection_code is ClaimRejectionCode.ROLE_MISMATCH


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
    assert result.assessment.blocking_failure is not None
    assert result.assessment.blocking_failure.operation == "assessment.claim_admission"
    assert (
        result.assessment.blocking_failure.code is AdapterErrorCode.MALFORMED_RESPONSE
    )
    assert (
        decide_disposition(result.assessment).disposition is Disposition.RETRY_PENDING
    )


def test_rejected_extra_claim_keeps_an_evidence_bounded_answer() -> None:
    admitted = _claim()
    rejected = _claim("missing-evidence").model_copy(
        update={
            "claim_id": "claim-extra",
            "statement": "The managed provider path has fully recovered.",
        },
    )

    result = build_case_assessment(
        context=_context(_resolved_identity()),
        proposal=AssessmentProposal(
            question="Is the workspace active?",
            answer_status=AnswerStatus.SUPPORTED,
            proposed_claims=(admitted, rejected),
        ),
        evidence=(_evidence(),),
        as_of=NOW,
    )

    assert result.assessment.answer_status is AnswerStatus.BOUNDED
    assert result.assessment.admitted_claims == (admitted,)
    assert result.admissions[0].accepted is True
    assert result.admissions[1].accepted is False
    assert result.assessment.blocking_failure is None
    assert decide_disposition(result.assessment).disposition is Disposition.RESOLVED


def test_claim_admission_pairs_results_by_position_not_duplicate_id_set() -> None:
    accepted = _claim()
    rejected = _claim("missing-evidence").model_copy(
        update={"statement": "The runtime has recovered."},
    )
    proposal = AssessmentProposal.model_construct(
        question="What is the current state?",
        answer_status=AnswerStatus.SUPPORTED,
        proposed_claims=(accepted, rejected),
    )

    result = build_case_assessment(
        context=_context(_resolved_identity()),
        proposal=proposal,
        evidence=(_evidence(),),
        as_of=NOW,
    )

    assert tuple(admission.accepted for admission in result.admissions) == (True, False)
    assert result.assessment.admitted_claims == (accepted,)


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
        update={
            "evidence_id": "ev-identity",
            "call_id": "ev-identity",
            "authority": EvidenceAuthority.TARGET_BINDING,
            "supports_roles": (),
        },
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

    assert (
        missing_identity.admissions[0].rejection_code
        is ClaimRejectionCode.MISSING_EVIDENCE
    )
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


def test_available_attachment_cannot_be_requested_again() -> None:
    attachment = AttachmentRef(
        attachment_id="attachment-1",
        filename="error.png",
        content_type="image/png",
        download_url="https://example.test/error.png",
    )
    context = _context(_resolved_identity()).model_copy(
        update={
            "ticket": _ticket().model_copy(update={"attachments": (attachment,)}),
            "visual_observations": (
                VisualObservation(
                    attachment_id="attachment-1",
                    evidence_id="visual:attachment-1",
                    visible_text="Quota exceeded",
                    ui_state="error dialog",
                    untrusted=True,
                ),
            ),
        },
    )

    result = build_case_assessment(
        context=context,
        proposal=AssessmentProposal(
            question="What does the screenshot show?",
            answer_status=AnswerStatus.INSUFFICIENT,
            proposed_customer_fields=("attachment_resend",),
        ),
        evidence=(),
        as_of=NOW,
    )

    assert result.assessment.customer_only_fields == ()
    assert result.rejected_customer_fields == ("attachment_resend",)


@pytest.mark.parametrize(
    ("status", "result_status", "supports_observation"),
    [
        (
            VisualObservationStatus.OBSERVED,
            EvidenceResultStatus.SUCCESS,
            True,
        ),
        (
            VisualObservationStatus.UNAVAILABLE,
            EvidenceResultStatus.FAILURE,
            False,
        ),
        (
            VisualObservationStatus.OMITTED,
            EvidenceResultStatus.PARTIAL,
            False,
        ),
        (
            VisualObservationStatus.NO_MODEL_RESULT,
            EvidenceResultStatus.FAILURE,
            False,
        ),
    ],
)
def test_only_observed_visuals_can_support_factual_claims(
    status: VisualObservationStatus,
    result_status: EvidenceResultStatus,
    supports_observation: bool,
) -> None:
    attachment = AttachmentRef(
        attachment_id="attachment-1",
        filename="error.png",
        content_type="image/png",
        download_url="https://example.test/error.png",
    )
    context = _context(_resolved_identity()).model_copy(
        update={
            "ticket": _ticket().model_copy(update={"attachments": (attachment,)}),
            "visual_observations": (
                VisualObservation(
                    attachment_id="attachment-1",
                    evidence_id="visual:attachment-1",
                    status=status,
                    visible_text=("Quota exceeded" if supports_observation else ""),
                    untrusted=True,
                ),
            ),
        },
    )

    evidence = build_case_context_evidence(context)
    visual = next(item for item in evidence if item.source == "case_context.visual")

    assert visual.result_status is result_status
    assert visual.supports_roles == (
        (ClaimRole.VISUAL_OBSERVATION,) if supports_observation else ()
    )


def test_visual_evidence_supports_only_a_narrow_visual_observation() -> None:
    attachment = AttachmentRef(
        attachment_id="attachment-1",
        filename="error.png",
        content_type="image/png",
        download_url="https://example.test/error.png",
    )
    context = _context(_resolved_identity()).model_copy(
        update={
            "ticket": _ticket().model_copy(update={"attachments": (attachment,)}),
            "visual_observations": (
                VisualObservation(
                    attachment_id="attachment-1",
                    evidence_id="visual:attachment-1",
                    visible_text="Quota exceeded",
                ),
            ),
        },
    )
    evidence = build_case_context_evidence(context)
    ticket_target = TargetReference(kind="ticket_id", value="4001")
    product_fact = Claim(
        claim_id="claim-product",
        statement="The product quota is exhausted.",
        role=ClaimRole.PRODUCT_FACT,
        status=ClaimStatus.EVIDENCE_SUPPORTED,
        supporting_evidence_ids=("visual:attachment-1",),
        targets=(ticket_target,),
        public_allowed=True,
    )
    visual_observation = product_fact.model_copy(
        update={
            "claim_id": "claim-visual",
            "statement": "The screenshot displays the text 'Quota exceeded'.",
            "role": ClaimRole.VISUAL_OBSERVATION,
        },
    )

    result = build_case_assessment(
        context=context,
        proposal=AssessmentProposal(
            question="What does the screenshot show?",
            answer_status=AnswerStatus.SUPPORTED,
            proposed_claims=(product_fact, visual_observation),
        ),
        evidence=evidence,
        as_of=NOW,
    )

    assert result.admissions[0].rejection_code is ClaimRejectionCode.ROLE_MISMATCH
    assert result.admissions[1].accepted is True


def test_missing_attachment_cannot_be_requested_as_a_resend() -> None:
    result = build_case_assessment(
        context=_context(_resolved_identity()),
        proposal=AssessmentProposal(
            question="Can you resend the screenshot?",
            answer_status=AnswerStatus.INSUFFICIENT,
            proposed_customer_fields=("attachment_resend",),
        ),
        evidence=(),
        as_of=NOW,
    )

    assert result.assessment.customer_only_fields == ()
    assert result.rejected_customer_fields == ("attachment_resend",)


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


def test_exhausted_identity_retry_creates_typed_support_ownership_packet() -> None:
    identity = IdentityResolution(
        contact_email="owner@example.com",
        target_identity="owner@example.com",
        status=ResolutionStatus.LOOKUP_FAILED,
        error_code=AdapterErrorCode.TIMEOUT,
        evidence_ids=("ev-timeout",),
        detail="support-side lookup timed out",
    )
    context = _context(identity).model_copy(update={"automation_attempt": 3})

    result = build_case_assessment(
        context=context,
        proposal=AssessmentProposal(
            question="Which workspace is affected?",
            answer_status=AnswerStatus.INSUFFICIENT,
            proposed_customer_fields=("workspace_id",),
        ),
        evidence=(),
        as_of=NOW,
    )
    decision = decide_disposition(result.assessment)

    assert result.assessment.blocking_failure is None
    assert result.assessment.customer_only_fields == ()
    assert result.assessment.human_action is not None
    assert result.assessment.human_action.ownership_action is OwnershipAction.SUPPORT
    assert (
        result.assessment.human_action.reason_code
        is ReasonCode.AUTOMATION_RETRY_EXHAUSTED
    )
    assert decision.ownership_action is OwnershipAction.SUPPORT
    assert decision.reason_code is ReasonCode.AUTOMATION_RETRY_EXHAUSTED


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


def test_identity_ambiguity_forces_workspace_choice_when_model_omits_it() -> None:
    """The safe clarification must not depend on the model proposing a field."""
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
            question="Why is my API quota 5,000?",
            answer_status=AnswerStatus.INSUFFICIENT,
        ),
        evidence=(),
        as_of=NOW,
    )
    decision = decide_disposition(result.assessment)

    assert result.assessment.blocking_failure is None
    assert result.assessment.customer_only_fields == ("target_workspace_choice",)
    assert decision.disposition is Disposition.AWAITING_CUSTOMER
    assert decision.required_customer_fields == ("target_workspace_choice",)


def test_supported_general_answer_does_not_carry_workspace_clarification() -> None:
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
    claim = Claim(
        claim_id="claim-docs",
        statement="Dify supports API requests from published applications.",
        role=ClaimRole.PRODUCT_FACT,
        status=ClaimStatus.CONFIRMED,
        supporting_evidence_ids=("ev-docs",),
        public_allowed=True,
    )
    evidence = EvidenceItem(
        evidence_id="ev-docs",
        call_id="call-docs",
        source="help-center",
        authority=EvidenceAuthority.CURRENT_SUPPORTING,
        result_status=EvidenceResultStatus.SUCCESS,
        observed_at=NOW,
        supports_roles=(ClaimRole.PRODUCT_FACT,),
        payload={"fact": "published applications can receive API requests"},
    )

    result = build_case_assessment(
        context=_context(identity),
        proposal=AssessmentProposal(
            question="Does Dify support API requests?",
            answer_status=AnswerStatus.SUPPORTED,
            proposed_claims=(claim,),
        ),
        evidence=(evidence,),
        as_of=NOW,
    )

    assert result.assessment.answer_status is AnswerStatus.SUPPORTED
    assert result.assessment.customer_only_fields == ()


def test_pending_identity_lookup_uses_retry_even_without_model_requested_field() -> (
    None
):
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
    assert (
        result.assessment.blocking_failure.code
        is AdapterErrorCode.TRANSPORT_UNAVAILABLE
    )


def test_failed_billing_lookup_becomes_internal_retry() -> None:
    context = CaseContext(
        ticket=_ticket(),
        built_at=NOW,
        identity=_resolved_identity(),
        billing_required=True,
        billing=BillingBinding(
            status=BillingBindingStatus.LOOKUP_FAILED,
            error_code=AdapterErrorCode.TIMEOUT,
            evidence_ids=("ev-identity", "ev-billing-timeout"),
            detail="tenant-scoped Billing lookup failed",
        ),
    )

    result = build_case_assessment(
        context=context,
        proposal=AssessmentProposal(
            question="What plan is active?",
            answer_status=AnswerStatus.INSUFFICIENT,
        ),
        evidence=(),
        as_of=NOW,
    )
    decision = decide_disposition(result.assessment)

    assert result.assessment.blocking_failure is not None
    assert result.assessment.blocking_failure.operation == "billing.lookup"
    assert result.assessment.blocking_failure.code is AdapterErrorCode.TIMEOUT
    assert decision.disposition is Disposition.RETRY_PENDING
    assert decision.ownership_action is OwnershipAction.NONE


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
            action_proposal=_action_proposal(
                ActionRequirement.CONFIRMED_PLATFORM_DEFECT
            ),
        ),
        evidence=(_evidence(),),
        as_of=NOW,
    )

    assert result.assessment.human_action is None
    assert (
        result.rejected_action_reason
        == "confirmed defect requires admitted boundary and root-cause claims"
    )
    assert (
        decide_disposition(result.assessment).ownership_action is OwnershipAction.NONE
    )


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
        tool_evidence(
            call_id="ev-boundary",
            tool_name="signoz_search_logs",
            args={"tenant": TENANT_1, "searchText": "collection missing"},
            allowed_target_arguments=frozenset({"tenant", "searchText"}),
            observed_at=NOW,
            success=True,
            output="The current tenant request fails at collection lookup.",
            error="",
        ),
        tool_evidence(
            call_id="ev-cause",
            tool_name="git_show",
            args={"repo": "api", "filepath": "retrieval.py", "ref": "runtime"},
            allowed_target_arguments=frozenset({"repo", "filepath", "ref"}),
            observed_at=NOW,
            success=True,
            output="Aligned source maps the configured collection before lookup.",
            error="",
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

    assert evidence[0].targets == (TARGET,)
    assert evidence[1].targets == ()
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
            update={
                "questions": (
                    "Please approve a refund for this renewal.",
                    "Workspace One",
                ),
                "active_question": "Please approve a refund for this renewal.",
            },
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


def test_release_date_question_does_not_imply_policy_commitment() -> None:
    context = _context(_resolved_identity()).model_copy(
        update={
            "ticket": _ticket().model_copy(
                update={"questions": ("When is the Cloud release date?",)},
            ),
        },
    )

    action, reason = admit_action_proposal(
        context=context,
        proposal=_action_proposal(ActionRequirement.POLICY_COMMITMENT),
        admitted_claims=(),
        eligible_evidence_ids=frozenset({"ev-current"}),
    )

    assert action is None
    assert "does not establish" in reason


def test_explicit_guarantee_request_can_require_policy_owner() -> None:
    context = _context(_resolved_identity()).model_copy(
        update={
            "ticket": _ticket().model_copy(
                update={"questions": ("Please guarantee release by Friday.",)},
            ),
        },
    )

    action, reason = admit_action_proposal(
        context=context,
        proposal=_action_proposal(ActionRequirement.POLICY_COMMITMENT),
        admitted_claims=(),
        eligible_evidence_ids=frozenset({"ev-current"}),
    )

    assert reason == ""
    assert action is not None
    assert action.ownership_action is OwnershipAction.SUPPORT


def test_refund_status_question_does_not_imply_new_billing_approval() -> None:
    identity = _resolved_identity()
    snapshot = BillingSnapshot(
        tenant_id=TENANT_1,
        identity_tenant_id=TENANT_1,
        plan="professional",
        observed_at=NOW,
        evidence_ids=("ev-identity", "ev-billing"),
    )
    context = CaseContext(
        ticket=_ticket().model_copy(
            update={"questions": ("Where is the refund I already requested?",)},
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

    action, reason = admit_action_proposal(
        context=context,
        proposal=_action_proposal(ActionRequirement.BUSINESS_APPROVAL),
        admitted_claims=(),
        eligible_evidence_ids=frozenset({"ev-current"}),
    )

    assert action is None
    assert "does not establish" in reason


@pytest.mark.parametrize(
    ("requirement", "question"),
    (
        (
            ActionRequirement.CUSTOMER_REQUESTED_HUMAN,
            "I do not want to talk to a human; please answer here.",
        ),
        (
            ActionRequirement.BUSINESS_APPROVAL,
            "I do not want a refund; just explain the invoice.",
        ),
        (
            ActionRequirement.SECURITY_OR_PRIVACY_REVIEW,
            "Where is your security documentation?",
        ),
        (
            ActionRequirement.SENSITIVE_ACCOUNT_MUTATION,
            "How do I delete a workflow node?",
        ),
    ),
)
def test_action_policy_rejects_negated_or_wrong_object_intent(
    requirement: ActionRequirement,
    question: str,
) -> None:
    context = _context(_resolved_identity()).model_copy(
        update={"ticket": _ticket().model_copy(update={"questions": (question,)})},
    )

    action, reason = admit_action_proposal(
        context=context,
        proposal=_action_proposal(requirement),
        admitted_claims=(),
        eligible_evidence_ids=frozenset({"ev-current"}),
    )

    assert action is None
    assert "does not establish" in reason


def test_action_packet_includes_all_required_defect_evidence() -> None:
    context = _context(_resolved_identity()).model_copy(
        update={
            "ticket": _ticket().model_copy(
                update={"questions": ("Why is the current request failing?",)},
            ),
        },
    )
    claims = (
        Claim(
            claim_id="claim-boundary",
            statement="The current request fails at the retrieval boundary.",
            role=ClaimRole.FAILING_BOUNDARY,
            status=ClaimStatus.CONFIRMED,
            supporting_evidence_ids=("ev-boundary",),
            targets=(TARGET,),
            public_allowed=True,
        ),
        Claim(
            claim_id="claim-cause",
            statement="The aligned runtime source confirms the mechanism.",
            role=ClaimRole.ROOT_CAUSE,
            status=ClaimStatus.CONFIRMED,
            supporting_evidence_ids=("ev-boundary", "ev-source"),
            targets=(TARGET,),
            public_allowed=True,
        ),
    )
    proposal = _action_proposal(
        ActionRequirement.CONFIRMED_PLATFORM_DEFECT,
    ).model_copy(update={"evidence_ids": ("ev-current",)})

    action, reason = admit_action_proposal(
        context=context,
        proposal=proposal,
        admitted_claims=claims,
        eligible_evidence_ids=frozenset(
            {"ev-current", "ev-boundary", "ev-source"},
        ),
    )

    assert reason == ""
    assert action is not None
    assert set(action.evidence_ids) == {"ev-current", "ev-boundary", "ev-source"}
