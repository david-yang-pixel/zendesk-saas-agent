from __future__ import annotations

import re

from zd_agent.casework.domain.enums import (
    ActionRequirement,
    BillingBindingStatus,
    ClaimRole,
    OwnershipAction,
    ReasonCode,
)
from zd_agent.casework.domain.models import (
    ActionProposal,
    CaseContext,
    Claim,
    HumanActionRequest,
)


_QUESTION_PATTERNS = {
    ActionRequirement.CUSTOMER_REQUESTED_HUMAN: re.compile(
        r"\b(?:human|person|representative|support engineer|人工|客服)\b",
        re.IGNORECASE,
    ),
    ActionRequirement.BUSINESS_APPROVAL: re.compile(
        r"\b(?:refund|credit|exception|contract|退款|退费|合同|例外)\b",
        re.IGNORECASE,
    ),
    ActionRequirement.SENSITIVE_ACCOUNT_MUTATION: re.compile(
        r"\b(?:delete|transfer|change owner|remove account|删除|转移|更换所有者)\b",
        re.IGNORECASE,
    ),
    ActionRequirement.SECURITY_OR_PRIVACY_REVIEW: re.compile(
        r"\b(?:security|privacy|breach|exposure|安全|隐私|泄露)\b",
        re.IGNORECASE,
    ),
    ActionRequirement.POLICY_COMMITMENT: re.compile(
        r"\b(?:commit|guarantee|sla|release date|deadline|承诺|保证|上线日期)\b",
        re.IGNORECASE,
    ),
}

_ACTION_MAPPING = {
    ActionRequirement.CUSTOMER_REQUESTED_HUMAN: (
        OwnershipAction.SUPPORT,
        ReasonCode.CUSTOMER_REQUESTED_HUMAN,
    ),
    ActionRequirement.BUSINESS_APPROVAL: (
        OwnershipAction.BILLING,
        ReasonCode.BUSINESS_APPROVAL_REQUIRED,
    ),
    ActionRequirement.SENSITIVE_ACCOUNT_MUTATION: (
        OwnershipAction.SUPPORT,
        ReasonCode.SENSITIVE_ACCOUNT_MUTATION_REQUIRED,
    ),
    ActionRequirement.CONFIRMED_PLATFORM_DEFECT: (
        OwnershipAction.ENGINEERING,
        ReasonCode.CONFIRMED_PLATFORM_DEFECT,
    ),
    ActionRequirement.CONFIRMED_SERVICE_INCIDENT: (
        OwnershipAction.SRE,
        ReasonCode.CONFIRMED_SERVICE_INCIDENT,
    ),
    ActionRequirement.SECURITY_OR_PRIVACY_REVIEW: (
        OwnershipAction.SECURITY,
        ReasonCode.SECURITY_OR_PRIVACY_REVIEW_REQUIRED,
    ),
    ActionRequirement.POLICY_COMMITMENT: (
        OwnershipAction.SUPPORT,
        ReasonCode.POLICY_COMMITMENT_REQUIRED,
    ),
}


def _required_claim_error(
    requirement: ActionRequirement,
    claims: tuple[Claim, ...],
) -> str:
    roles = {claim.role for claim in claims}
    if requirement is ActionRequirement.CONFIRMED_PLATFORM_DEFECT:
        required = {ClaimRole.FAILING_BOUNDARY, ClaimRole.ROOT_CAUSE}
        if not required.issubset(roles):
            return "confirmed defect requires admitted boundary and root-cause claims"
    if requirement is ActionRequirement.CONFIRMED_SERVICE_INCIDENT:
        required = {ClaimRole.FAILING_BOUNDARY, ClaimRole.IMPACT}
        if not required.issubset(roles):
            return "confirmed incident requires admitted boundary and impact claims"
    return ""


def _question_error(requirement: ActionRequirement, question: str) -> str:
    pattern = _QUESTION_PATTERNS.get(requirement)
    if pattern is None or pattern.search(question):
        return ""
    return f"customer question does not establish {requirement.value}"


def _billing_error(requirement: ActionRequirement, context: CaseContext) -> str:
    if requirement is not ActionRequirement.BUSINESS_APPROVAL:
        return ""
    if context.billing and context.billing.status is BillingBindingStatus.RESOLVED:
        return ""
    return "business approval requires resolved tenant-bound Billing context"


def admit_action_proposal(
    *,
    context: CaseContext,
    proposal: ActionProposal | None,
    admitted_claims: tuple[Claim, ...],
    eligible_evidence_ids: frozenset[str],
) -> tuple[HumanActionRequest | None, str]:
    """Map a proven action need to ownership; the model never picks the team."""
    if proposal is None:
        return None, ""
    errors = (
        _required_claim_error(proposal.requirement, admitted_claims),
        _question_error(proposal.requirement, context.ticket.questions[-1]),
        _billing_error(proposal.requirement, context),
    )
    if error := next((value for value in errors if value), ""):
        return None, error
    if not set(proposal.evidence_ids).issubset(eligible_evidence_ids):
        return None, "action proposal cites evidence that was not admitted"
    owner, reason = _ACTION_MAPPING[proposal.requirement]
    return HumanActionRequest(
        ownership_action=owner,
        reason_code=reason,
        summary=proposal.summary,
        customer_impact=proposal.customer_impact,
        completed_checks=proposal.completed_checks,
        unresolved_action=proposal.unresolved_action,
        acceptance_criteria=proposal.acceptance_criteria,
        evidence_ids=proposal.evidence_ids,
    ), ""
