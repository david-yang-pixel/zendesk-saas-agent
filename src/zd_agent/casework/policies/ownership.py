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
    ActionRequirement.SENSITIVE_ACCOUNT_MUTATION: re.compile(
        r"\b(?:delete|transfer|change owner|remove account|删除|转移|更换所有者)\b",
        re.IGNORECASE,
    ),
    ActionRequirement.SECURITY_OR_PRIVACY_REVIEW: re.compile(
        r"\b(?:security|privacy|breach|exposure|安全|隐私|泄露)\b",
        re.IGNORECASE,
    ),
}

_HUMAN_SUBJECT_RE = re.compile(
    r"\b(?:human|person|representative|support engineer)\b|人工|客服",
    re.IGNORECASE,
)
_HUMAN_REQUEST_RE = re.compile(
    r"\b(?:speak|talk|connect|transfer|escalate|want|need|request)\b|"
    r"转接|联系|需要|要求",
    re.IGNORECASE,
)
_BUSINESS_SUBJECT_RE = re.compile(
    r"\b(?:refund|refunded|billing credit|account credit|credit note|"
    r"contractual exception)\b|退款|退费|账单抵扣|合同例外",
    re.IGNORECASE,
)
_BUSINESS_REQUEST_RE = re.compile(
    r"\b(?:approve|issue|grant|initiate|request|want|would like|"
    r"can (?:i|we|you)|could (?:i|we|you))\b|申请|办理|批准|发起|希望|想要|可以",
    re.IGNORECASE,
)
_COMMITMENT_RE = re.compile(
    r"\b(?:commit|guarantee|promise|sla commitment)\b|承诺|保证",
    re.IGNORECASE,
)

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


def _question_establishes(requirement: ActionRequirement, question: str) -> bool:
    if requirement is ActionRequirement.CUSTOMER_REQUESTED_HUMAN:
        return bool(
            _HUMAN_SUBJECT_RE.search(question) and _HUMAN_REQUEST_RE.search(question),
        )
    if requirement is ActionRequirement.BUSINESS_APPROVAL:
        return bool(
            _BUSINESS_SUBJECT_RE.search(question)
            and _BUSINESS_REQUEST_RE.search(question),
        )
    if requirement is ActionRequirement.POLICY_COMMITMENT:
        return bool(_COMMITMENT_RE.search(question))
    pattern = _QUESTION_PATTERNS.get(requirement)
    return pattern is None or bool(pattern.search(question))


def _question_error(requirement: ActionRequirement, question: str) -> str:
    if _question_establishes(requirement, question):
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
