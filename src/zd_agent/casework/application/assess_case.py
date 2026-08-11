from __future__ import annotations

from datetime import datetime

from zd_agent.casework.domain.enums import (
    AdapterErrorCode,
    AnswerStatus,
    BillingBindingStatus,
    ClaimRole,
    ResolutionStatus,
)
from zd_agent.casework.domain.models import (
    AdapterFailure,
    AssessmentBuild,
    AssessmentProposal,
    CaseAssessment,
    CaseContext,
    Claim,
    ClaimAdmission,
    EvidenceItem,
    HumanActionRequest,
    TargetReference,
)
from zd_agent.casework.policies.evidence import admit_claim
from zd_agent.casework.policies.customer_input import admit_customer_fields
from zd_agent.casework.policies.ownership import admit_action_proposal


def _admit_claims(
    *,
    context: CaseContext,
    proposal: AssessmentProposal,
    evidence: tuple[EvidenceItem, ...],
    as_of: datetime,
) -> tuple[tuple[ClaimAdmission, ...], tuple[Claim, ...]]:
    admissions = tuple(
        admit_claim(
            claim=claim,
            evidence=evidence,
            as_of=as_of,
            required_targets=_required_targets(context, claim),
            required_evidence_groups=_required_evidence_groups(context, claim),
        )
        for claim in proposal.proposed_claims
    )
    accepted_ids = {
        admission.claim_id for admission in admissions if admission.accepted
    }
    claims = tuple(
        claim for claim in proposal.proposed_claims if claim.claim_id in accepted_ids
    )
    return admissions, claims


_TENANT_BOUND_ROLES = {
    ClaimRole.CURRENT_STATE,
    ClaimRole.FAILING_BOUNDARY,
    ClaimRole.ROOT_CAUSE,
    ClaimRole.IMPACT,
    ClaimRole.RECOVERY,
}


def _required_targets(
    context: CaseContext,
    claim: Claim,
) -> tuple[TargetReference, ...]:
    if (
        context.identity.status is ResolutionStatus.RESOLVED
        and claim.role in _TENANT_BOUND_ROLES
    ):
        return (
            TargetReference(
                kind="tenant_id",
                value=context.identity.selected_tenant_id,
            ),
        )
    return ()


def _required_evidence_groups(
    context: CaseContext,
    claim: Claim,
) -> tuple[frozenset[str], ...]:
    billing = context.billing
    if (
        claim.role is not ClaimRole.CURRENT_STATE
        or billing is None
        or billing.status is not BillingBindingStatus.RESOLVED
    ):
        return ()
    identity_ids = frozenset(context.identity.evidence_ids)
    billing_ids = frozenset(billing.evidence_ids) - identity_ids
    return (identity_ids, billing_ids) if identity_ids and billing_ids else ()


def _customer_fields(
    *,
    context: CaseContext,
    proposal: AssessmentProposal,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    return admit_customer_fields(
        proposed_fields=proposal.proposed_customer_fields,
        identity_status=context.identity.status,
        attachment_resend_allowed=_attachment_resend_allowed(context),
    )


def _attachment_resend_allowed(context: CaseContext) -> bool:
    if not context.ticket.attachments:
        return True
    return any(
        "could not be retrieved" in observation.uncertainty.casefold()
        for observation in context.visual_observations
    )


def _identity_context_failure(
    *,
    context: CaseContext,
) -> AdapterFailure | None:
    status = context.identity.status
    if status not in {ResolutionStatus.LOOKUP_PENDING, ResolutionStatus.LOOKUP_FAILED}:
        return None
    code = (
        context.identity.error_code
        if status is ResolutionStatus.LOOKUP_FAILED
        else AdapterErrorCode.TRANSPORT_UNAVAILABLE
    )
    return AdapterFailure(
        operation="identity.lookup",
        code=code or AdapterErrorCode.PERMANENT_FAILURE,
        attempt=1,
        max_attempts=3,
        retry_after_seconds=2,
        evidence_id=(context.identity.evidence_ids[-1] if context.identity.evidence_ids else ""),
        detail=context.identity.detail,
    )


def _billing_context_failure(context: CaseContext) -> AdapterFailure | None:
    billing = context.billing
    if billing is None or billing.status in {
        BillingBindingStatus.RESOLVED,
        BillingBindingStatus.IDENTITY_UNRESOLVED,
    }:
        return None
    code = {
        BillingBindingStatus.LOOKUP_PENDING: AdapterErrorCode.TRANSPORT_UNAVAILABLE,
        BillingBindingStatus.LOOKUP_FAILED: (
            billing.error_code or AdapterErrorCode.PERMANENT_FAILURE
        ),
        BillingBindingStatus.TARGET_MISMATCH: AdapterErrorCode.MALFORMED_RESPONSE,
    }[billing.status]
    return AdapterFailure(
        operation="billing.lookup",
        code=code,
        attempt=1,
        max_attempts=3,
        retry_after_seconds=2,
        evidence_id=(billing.evidence_ids[-1] if billing.evidence_ids else ""),
        detail=billing.detail,
    )


def _blocking_failure(
    *,
    context: CaseContext,
    proposal: AssessmentProposal,
    answer_status: AnswerStatus,
) -> AdapterFailure | None:
    if proposal.blocking_failure is not None:
        return proposal.blocking_failure
    if answer_status is not AnswerStatus.INSUFFICIENT:
        return None
    return _identity_context_failure(
        context=context,
    ) or _billing_context_failure(context)


def _answer_status(
    proposal: AssessmentProposal,
    admissions: tuple[ClaimAdmission, ...],
) -> AnswerStatus:
    rejected_material = any(
        not admission.accepted and claim.material
        for claim, admission in zip(proposal.proposed_claims, admissions)
    )
    return AnswerStatus.INSUFFICIENT if rejected_material else proposal.answer_status


def _action(
    *,
    context: CaseContext,
    proposal: AssessmentProposal,
    admitted_claims: tuple[Claim, ...],
) -> tuple[HumanActionRequest | None, str]:
    eligible = frozenset(
        evidence_id
        for claim in admitted_claims
        for evidence_id in claim.supporting_evidence_ids
    ) | frozenset(context.identity.evidence_ids)
    if context.billing is not None:
        eligible |= frozenset(context.billing.evidence_ids)
    return admit_action_proposal(
        context=context,
        proposal=proposal.action_proposal,
        admitted_claims=admitted_claims,
        eligible_evidence_ids=eligible,
    )


def build_case_assessment(
    *,
    context: CaseContext,
    proposal: AssessmentProposal,
    evidence: tuple[EvidenceItem, ...],
    as_of: datetime,
) -> AssessmentBuild:
    """Convert a model proposal into policy-admitted support assessment."""
    admissions, admitted_claims = _admit_claims(
        context=context,
        proposal=proposal,
        evidence=evidence,
        as_of=as_of,
    )
    answer_status = _answer_status(proposal, admissions)
    customer_fields, rejected_fields = _customer_fields(
        context=context,
        proposal=proposal,
    )
    human_action, rejected_action_reason = _action(
        context=context,
        proposal=proposal,
        admitted_claims=admitted_claims,
    )
    assessment = CaseAssessment(
        question=proposal.question,
        answer_status=answer_status,
        admitted_claims=admitted_claims,
        customer_only_fields=customer_fields,
        blocking_failure=_blocking_failure(
            context=context,
            proposal=proposal,
            answer_status=answer_status,
        ),
        human_action=human_action,
        evidence_ids=tuple(
            dict.fromkeys(
                (*proposal.evidence_ids, *context.identity.evidence_ids),
            ),
        ),
        close_after_reply=proposal.close_after_reply,
    )
    return AssessmentBuild(
        assessment=assessment,
        admissions=admissions,
        rejected_customer_fields=rejected_fields,
        rejected_action_reason=rejected_action_reason,
    )
