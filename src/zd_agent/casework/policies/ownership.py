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


_SENSITIVE_OBJECT_RE = re.compile(
    r"\b(?:account|workspace|organization|organisation|team|member|ownership)\b|"
    r"账户|账号|工作空间|组织|团队|成员|所有权",
    re.IGNORECASE,
)
_SENSITIVE_MUTATION_RE = re.compile(
    r"\b(?:delete|remove|transfer|change\s+(?:the\s+)?owner|"
    r"replace\s+(?:the\s+)?owner)\b|删除|移除|转移|更换所有者",
    re.IGNORECASE,
)
_SECURITY_INCIDENT_RE = re.compile(
    r"\b(?:security|privacy)\s+(?:incident|issue|review|investigation)\b|"
    r"\b(?:breach|data leak|exposure|compromise|unauthorized access|"
    r"privacy violation)\b|安全事件|隐私事件|数据泄露|未授权访问",
    re.IGNORECASE,
)
_SECURITY_REVIEW_REQUEST_RE = re.compile(
    r"\b(?:please|request|need|want|would like)\b.{0,45}\b"
    r"(?:security|privacy)\b.{0,35}\b(?:review|investigate|assessment)\b|"
    r"请.{0,12}(?:安全|隐私).{0,12}(?:审查|调查|评估)",
    re.IGNORECASE,
)

_HUMAN_ROLE = (
    r"(?:a\s+(?:human(?:\s+support\s+engineer)?|person|representative|"
    r"support\s+(?:agent|engineer))|human\s+support|"
    r"support\s+(?:agent|engineer)|representative)"
)
_EXPLICIT_HUMAN_TAKEOVER_RE = re.compile(
    rf"(?:\b(?:i|we)\s+(?:want|need|would\s+like)\s+(?:to\s+)?"
    rf"(?:speak|talk)\s+(?:to|with)\s+{_HUMAN_ROLE}\b|"
    rf"\b(?:i|we)\s+(?:want|need|would\s+like)\s+{_HUMAN_ROLE}\b|"
    rf"\b(?:please\s+)?(?:connect|transfer|escalate)\s+"
    rf"(?:me|us|this\s+ticket)\s+(?:to|with)\s+{_HUMAN_ROLE}\b|"
    rf"\b(?:can|could|would)\s+you\s+(?:please\s+)?"
    rf"(?:connect|transfer|escalate)\s+(?:me|us|this\s+ticket)\s+"
    rf"(?:to|with)\s+{_HUMAN_ROLE}\b)|"
    r"(?:请)?(?:转|转接|联系)(?:到)?(?:人工|人工客服|客服)",
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
_NEGATED_INTENT_RE = re.compile(
    r"\b(?:do\s+not|don't|dont|did\s+not|didn't|never|not\s+want|"
    r"no\s+longer\s+want)\b|不想|不要|无需|不需要|没有要求",
    re.IGNORECASE,
)
_DIRECT_HUMAN_COMMANDS = frozenset(
    {
        "human",
        "human support",
        "support agent",
        "support engineer",
        "representative",
        "人工",
        "转人工",
        "人工客服",
    },
)
_DIRECT_HUMAN_NEGATION_RE = re.compile(
    rf"\b(?:i|we)\s+(?:do\s+not|don't|dont|no\s+longer)\s+"
    rf"(?:want|need)\s+(?:to\s+(?:speak|talk)\s+(?:to|with)\s+)?{_HUMAN_ROLE}\b|"
    rf"\b(?:please\s+)?(?:do\s+not|don't|dont)\s+"
    rf"(?:connect|transfer|escalate)\s+(?:me|us|this\s+ticket)\s+"
    rf"(?:to|with)\s+{_HUMAN_ROLE}\b|"
    r"\b(?:no\s+)?(?:human|representative|support\s+(?:agent|engineer))\s+"
    r"(?:is\s+)?(?:not\s+needed|not\s+required|needed|required)\b|"
    r"(?:不要|不需要|无需).{0,8}(?:人工|人工客服|客服)",
    re.IGNORECASE,
)
_HUMAN_ROLE_TOKEN_RE = re.compile(
    r"\b(?:human|person|representative|support\s+(?:agent|engineer))\b|"
    r"人工|客服",
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


def customer_requested_human(question: str) -> bool:
    """Recognize the advertised command and explicit non-negated human requests."""
    raw = " ".join(str(question or "").split()).strip()
    if not raw or _DIRECT_HUMAN_NEGATION_RE.search(raw):
        return False
    normalized = raw.casefold().strip(" \t\r\n.!,?;:\"'\u2018\u2019\u201c\u201d")
    if normalized in _DIRECT_HUMAN_COMMANDS:
        return True
    return bool(_EXPLICIT_HUMAN_TAKEOVER_RE.search(raw))


def _customer_cancelled_human_request(message: str) -> bool:
    if not _HUMAN_ROLE_TOKEN_RE.search(message):
        return False
    return bool(_DIRECT_HUMAN_NEGATION_RE.search(message))


def customer_human_request_active(messages: tuple[str, ...]) -> bool:
    """Fold ordered customer issue messages into sticky takeover intent."""
    return active_human_request_index(messages) is not None


def active_human_request_index(messages: tuple[str, ...]) -> int | None:
    """Return the active command index, cleared only by explicit cancellation."""
    requested = False
    request_index: int | None = None
    for index, message in enumerate(messages):
        if customer_requested_human(message):
            requested = True
            request_index = index
        elif _customer_cancelled_human_request(message):
            requested = False
            request_index = None
    return request_index if requested else None


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


def _required_action_evidence_ids(
    requirement: ActionRequirement,
    claims: tuple[Claim, ...],
    context: CaseContext,
) -> tuple[str, ...]:
    required_roles: frozenset[ClaimRole] = frozenset()
    if requirement is ActionRequirement.CONFIRMED_PLATFORM_DEFECT:
        required_roles = frozenset({ClaimRole.FAILING_BOUNDARY, ClaimRole.ROOT_CAUSE})
    elif requirement is ActionRequirement.CONFIRMED_SERVICE_INCIDENT:
        required_roles = frozenset({ClaimRole.FAILING_BOUNDARY, ClaimRole.IMPACT})
    evidence_ids = tuple(
        dict.fromkeys(
            evidence_id
            for claim in claims
            if claim.role in required_roles
            for evidence_id in claim.supporting_evidence_ids
        ),
    )
    if requirement is ActionRequirement.BUSINESS_APPROVAL and context.billing:
        identity_ids = set(context.identity.evidence_ids)
        billing_ids = tuple(
            evidence_id
            for evidence_id in context.billing.evidence_ids
            if evidence_id not in identity_ids
        )
        evidence_ids = tuple(dict.fromkeys((*evidence_ids, *billing_ids)))
    return evidence_ids


def _question_establishes(requirement: ActionRequirement, question: str) -> bool:
    if _NEGATED_INTENT_RE.search(question):
        return False
    if requirement is ActionRequirement.CUSTOMER_REQUESTED_HUMAN:
        return customer_requested_human(question)
    if requirement is ActionRequirement.BUSINESS_APPROVAL:
        return bool(
            _BUSINESS_SUBJECT_RE.search(question)
            and _BUSINESS_REQUEST_RE.search(question),
        )
    if requirement is ActionRequirement.POLICY_COMMITMENT:
        return bool(_COMMITMENT_RE.search(question))
    if requirement is ActionRequirement.SENSITIVE_ACCOUNT_MUTATION:
        return bool(
            _SENSITIVE_OBJECT_RE.search(question)
            and _SENSITIVE_MUTATION_RE.search(question)
        )
    if requirement is ActionRequirement.SECURITY_OR_PRIVACY_REVIEW:
        return bool(
            _SECURITY_INCIDENT_RE.search(question)
            or _SECURITY_REVIEW_REQUEST_RE.search(question)
        )
    return True


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
        _question_error(
            proposal.requirement,
            context.ticket.active_question or context.ticket.questions[-1],
        ),
        _billing_error(proposal.requirement, context),
    )
    if error := next((value for value in errors if value), ""):
        return None, error
    if not set(proposal.evidence_ids).issubset(eligible_evidence_ids):
        return None, "action proposal cites evidence that was not admitted"
    required_evidence_ids = _required_action_evidence_ids(
        proposal.requirement,
        admitted_claims,
        context,
    )
    if not set(required_evidence_ids).issubset(eligible_evidence_ids):
        return None, "required action evidence was not admitted"
    owner, reason = _ACTION_MAPPING[proposal.requirement]
    return HumanActionRequest(
        ownership_action=owner,
        reason_code=reason,
        summary=proposal.summary,
        customer_impact=proposal.customer_impact,
        completed_checks=proposal.completed_checks,
        unresolved_action=proposal.unresolved_action,
        acceptance_criteria=proposal.acceptance_criteria,
        evidence_ids=tuple(
            dict.fromkeys((*proposal.evidence_ids, *required_evidence_ids)),
        ),
    ), ""
