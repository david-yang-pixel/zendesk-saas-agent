from __future__ import annotations

from datetime import datetime

from zd_agent.casework.domain.enums import (
    AdapterErrorCode,
    AnswerStatus,
    BillingBindingStatus,
    ClaimRole,
    OwnershipAction,
    ReasonCode,
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
from zd_agent.casework.application.customer_clarification import (
    admit_case_customer_fields,
)
from zd_agent.casework.policies.evidence import admit_claim
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
    if claim.role is ClaimRole.VISUAL_OBSERVATION:
        return (
            TargetReference(
                kind="ticket_id",
                value=str(context.ticket.ticket_id),
            ),
        )
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
        attempt=min(context.automation_attempt, 3),
        max_attempts=3,
        retry_after_seconds=2,
        evidence_id=(
            context.identity.evidence_ids[-1] if context.identity.evidence_ids else ""
        ),
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
        attempt=min(context.automation_attempt, 3),
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
    return _identity_context_failure(context=context) or _billing_context_failure(
        context,
    )


def _retry_exhausted_action(
    failure: AdapterFailure | None,
) -> HumanActionRequest | None:
    if failure is None or not failure.retry_exhausted() or not failure.evidence_id:
        return None
    return HumanActionRequest(
        ownership_action=OwnershipAction.SUPPORT,
        reason_code=ReasonCode.AUTOMATION_RETRY_EXHAUSTED,
        summary=(
            f"Support-side verification remained unavailable after "
            f"{failure.attempt} attempts."
        ),
        customer_impact="The customer's latest question cannot yet be answered safely.",
        completed_checks=(f"Retried {failure.operation} {failure.attempt} times.",),
        unresolved_action=(
            f"Complete {failure.operation} and answer the latest customer question."
        ),
        acceptance_criteria=(
            "The target facts are verified from an authoritative source.",
            "The customer receives an evidence-grounded answer.",
        ),
        evidence_ids=(failure.evidence_id,),
    )


def _answer_status(
    proposal: AssessmentProposal,
    admissions: tuple[ClaimAdmission, ...],
) -> AnswerStatus:
    material = tuple(
        admission
        for claim, admission in zip(proposal.proposed_claims, admissions)
        if claim.material
    )
    if all(admission.accepted for admission in material):
        return proposal.answer_status
    if any(admission.accepted for admission in material) and proposal.answer_status in {
        AnswerStatus.SUPPORTED,
        AnswerStatus.BOUNDED,
    }:
        return AnswerStatus.BOUNDED
    return AnswerStatus.INSUFFICIENT


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


def _action_and_failure(
    *,
    context: CaseContext,
    proposal: AssessmentProposal,
    admitted_claims: tuple[Claim, ...],
    answer_status: AnswerStatus,
) -> tuple[HumanActionRequest | None, AdapterFailure | None, str]:
    action, rejection = _action(
        context=context,
        proposal=proposal,
        admitted_claims=admitted_claims,
    )
    failure = _blocking_failure(
        context=context,
        proposal=proposal,
        answer_status=answer_status,
    )
    if action is None and (exhausted := _retry_exhausted_action(failure)) is not None:
        return exhausted, None, rejection
    return action, failure, rejection


def _invalid_proposal_failure(
    *,
    context: CaseContext,
    admissions: tuple[ClaimAdmission, ...],
) -> AdapterFailure:
    rejected = tuple(
        admission.rejection_code.value
        for admission in admissions
        if not admission.accepted
    )
    evidence_id = (
        context.identity.evidence_ids[-1]
        if context.identity.evidence_ids
        else f"ticket:{context.ticket.ticket_id}:generation:{context.ticket.generation}"
    )
    return AdapterFailure(
        operation="assessment.claim_admission",
        code=AdapterErrorCode.MALFORMED_RESPONSE,
        attempt=min(context.automation_attempt, 3),
        max_attempts=3,
        retry_after_seconds=2,
        evidence_id=evidence_id,
        detail=(
            "model proposal had no safe disposition"
            + (f"; rejected claims: {', '.join(rejected)}" if rejected else "")
        ),
    )


def _ensure_safe_outcome(
    *,
    context: CaseContext,
    admissions: tuple[ClaimAdmission, ...],
    answer_status: AnswerStatus,
    admitted_claims: tuple[Claim, ...],
    customer_fields: tuple[str, ...],
    human_action: HumanActionRequest | None,
    blocking_failure: AdapterFailure | None,
) -> tuple[HumanActionRequest | None, AdapterFailure | None]:
    answerable = bool(
        admitted_claims
        and answer_status in {AnswerStatus.SUPPORTED, AnswerStatus.BOUNDED}
    )
    if answerable or customer_fields or human_action or blocking_failure:
        return human_action, blocking_failure
    failure = _invalid_proposal_failure(context=context, admissions=admissions)
    exhausted = _retry_exhausted_action(failure)
    return (exhausted, None) if exhausted is not None else (None, failure)


def _case_assessment(
    *,
    context: CaseContext,
    proposal: AssessmentProposal,
    answer_status: AnswerStatus,
    admitted_claims: tuple[Claim, ...],
    customer_fields: tuple[str, ...],
    human_action: HumanActionRequest | None,
    blocking_failure: AdapterFailure | None,
) -> CaseAssessment:
    return CaseAssessment(
        question=proposal.question,
        answer_status=answer_status,
        admitted_claims=admitted_claims,
        customer_only_fields=customer_fields,
        blocking_failure=blocking_failure,
        human_action=human_action,
        evidence_ids=tuple(
            dict.fromkeys((*proposal.evidence_ids, *context.identity.evidence_ids)),
        ),
        close_after_reply=proposal.close_after_reply,
    )


def build_case_assessment(
    *,
    context: CaseContext,
    proposal: AssessmentProposal,
    evidence: tuple[EvidenceItem, ...],
    as_of: datetime,
) -> AssessmentBuild:
    admissions, admitted_claims = _admit_claims(
        context=context, proposal=proposal, evidence=evidence, as_of=as_of
    )
    answer_status = _answer_status(proposal, admissions)
    human_action, blocking_failure, rejected_action_reason = _action_and_failure(
        context=context,
        proposal=proposal,
        admitted_claims=admitted_claims,
        answer_status=answer_status,
    )
    customer_fields, rejected_fields = admit_case_customer_fields(
        context=context,
        proposal=proposal,
        answer_status=answer_status,
        human_action=human_action,
        blocking_failure=blocking_failure,
    )
    human_action, blocking_failure = _ensure_safe_outcome(
        context=context,
        admissions=admissions,
        answer_status=answer_status,
        admitted_claims=admitted_claims,
        customer_fields=customer_fields,
        human_action=human_action,
        blocking_failure=blocking_failure,
    )
    assessment = _case_assessment(
        context=context,
        proposal=proposal,
        answer_status=answer_status,
        admitted_claims=admitted_claims,
        customer_fields=customer_fields,
        human_action=human_action,
        blocking_failure=blocking_failure,
    )
    return AssessmentBuild(
        assessment=assessment,
        admissions=admissions,
        rejected_customer_fields=rejected_fields,
        rejected_action_reason=rejected_action_reason,
    )
