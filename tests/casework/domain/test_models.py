from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from zd_agent.casework.domain.enums import (
    AnswerStatus,
    ClaimRole,
    ClaimStatus,
    Disposition,
    EvidenceAuthority,
    EvidenceResultStatus,
    OwnershipAction,
    ReasonCode,
    ReplyKind,
    ResolutionMethod,
    ResolutionStatus,
)
from zd_agent.casework.domain.models import (
    AssessmentProposal,
    BillingSnapshot,
    Claim,
    DispositionDecision,
    EvidenceItem,
    IdentityResolution,
    RetryDirective,
    TargetReference,
    WorkspaceCandidate,
)


NOW = datetime(2026, 8, 11, 2, 0, tzinfo=timezone.utc)


def test_assessment_proposal_rejects_duplicate_claim_ids() -> None:
    claim = Claim(
        claim_id="claim-duplicate",
        statement="The workspace is active.",
        role=ClaimRole.CURRENT_STATE,
        status=ClaimStatus.CONFIRMED,
        supporting_evidence_ids=("ev-current",),
        public_allowed=True,
    )

    with pytest.raises(ValidationError, match="duplicate claim IDs"):
        AssessmentProposal(
            question="What is the current state?",
            answer_status=AnswerStatus.SUPPORTED,
            proposed_claims=(
                claim,
                claim.model_copy(update={"statement": "The runtime recovered."}),
            ),
        )


def test_identity_resolution_is_immutable_and_target_bound() -> None:
    candidate = WorkspaceCandidate(
        tenant_id="tenant-1",
        workspace_name="Acme",
        role="owner",
    )
    resolution = IdentityResolution(
        contact_email="Owner@Example.com",
        target_identity="owner@example.com",
        status=ResolutionStatus.RESOLVED,
        method=ResolutionMethod.UNIQUE_EMAIL_MEMBERSHIP,
        candidates=(candidate,),
        selected_tenant_id="tenant-1",
        selected_workspace_name="Acme",
        selected_role="owner",
        evidence_ids=("ev-identity",),
    )

    assert resolution.contact_email == "owner@example.com"
    assert resolution.selected_tenant_id == "tenant-1"
    with pytest.raises(ValidationError):
        resolution.selected_tenant_id = "tenant-2"


def test_resolved_identity_requires_selected_candidate_and_evidence() -> None:
    with pytest.raises(ValidationError, match="selected tenant"):
        IdentityResolution(
            contact_email="owner@example.com",
            target_identity="owner@example.com",
            status=ResolutionStatus.RESOLVED,
            method=ResolutionMethod.UNIQUE_EMAIL_MEMBERSHIP,
            candidates=(),
            selected_tenant_id="tenant-1",
            evidence_ids=("ev-identity",),
        )


def test_billing_snapshot_must_match_resolved_tenant() -> None:
    with pytest.raises(ValidationError, match="target tenant"):
        BillingSnapshot(
            tenant_id="tenant-2",
            plan="professional",
            subscription_status="active",
            observed_at=NOW,
            evidence_ids=("ev-billing",),
            identity_tenant_id="tenant-1",
        )


def test_material_evidence_keeps_structured_payload_without_any_preview_contract() -> (
    None
):
    item = EvidenceItem(
        evidence_id="ev-billing",
        call_id="call-1",
        source="billing_reader",
        authority=EvidenceAuthority.CURRENT_AUTHORITATIVE,
        result_status=EvidenceResultStatus.SUCCESS,
        targets=(TargetReference(kind="tenant_id", value="tenant-1"),),
        observed_at=NOW,
        payload={"plan": "professional", "active": True},
        redacted=True,
    )

    assert item.payload == {"plan": "professional", "active": True}
    assert item.target_value("tenant_id") == "tenant-1"


def test_resolved_decision_cannot_transfer_ownership() -> None:
    with pytest.raises(ValidationError, match="resolved disposition"):
        DispositionDecision(
            disposition=Disposition.RESOLVED,
            reply_kind=ReplyKind.ANSWER,
            ownership_action=OwnershipAction.SUPPORT,
            reason_code=ReasonCode.RESOLVED_WITH_EVIDENCE,
            evidence_ids=("ev-1",),
        )


def test_customer_ask_requires_exact_customer_owned_fields() -> None:
    with pytest.raises(ValidationError, match="customer fields"):
        DispositionDecision(
            disposition=Disposition.AWAITING_CUSTOMER,
            reply_kind=ReplyKind.ASK,
            reason_code=ReasonCode.CUSTOMER_INPUT_REQUIRED,
            evidence_ids=("ev-1",),
        )


def test_retry_pending_requires_retry_directive_and_no_ownership() -> None:
    decision = DispositionDecision(
        disposition=Disposition.RETRY_PENDING,
        reply_kind=ReplyKind.NONE,
        reason_code=ReasonCode.INTERNAL_RETRY_PENDING,
        evidence_ids=("ev-error",),
        retry_directive=RetryDirective(
            operation="billing.lookup",
            attempt=1,
            max_attempts=3,
            retry_after_seconds=2.0,
        ),
    )

    assert decision.ownership_action is OwnershipAction.NONE
    assert decision.retry_directive is not None


def test_engineering_escalation_requires_matching_ownership_and_reason() -> None:
    with pytest.raises(ValidationError, match="engineering ownership"):
        DispositionDecision(
            disposition=Disposition.ENGINEERING_ESCALATION,
            reply_kind=ReplyKind.ANSWER,
            ownership_action=OwnershipAction.SUPPORT,
            reason_code=ReasonCode.CONFIRMED_PLATFORM_DEFECT,
            evidence_ids=("ev-boundary", "ev-source"),
        )

    decision = DispositionDecision(
        disposition=Disposition.ENGINEERING_ESCALATION,
        reply_kind=ReplyKind.ANSWER,
        ownership_action=OwnershipAction.ENGINEERING,
        reason_code=ReasonCode.CONFIRMED_PLATFORM_DEFECT,
        evidence_ids=("ev-boundary", "ev-source"),
    )

    assert decision.ownership_action is OwnershipAction.ENGINEERING
