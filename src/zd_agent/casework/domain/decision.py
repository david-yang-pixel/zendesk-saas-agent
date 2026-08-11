from __future__ import annotations

from pydantic import Field, field_validator, model_validator

from zd_agent.casework.domain.base import FrozenModel
from zd_agent.casework.domain.enums import (
    Disposition,
    OwnershipAction,
    ReasonCode,
    ReplyKind,
)


class RetryDirective(FrozenModel):
    operation: str = Field(min_length=1)
    attempt: int = Field(ge=1)
    max_attempts: int = Field(ge=1)
    retry_after_seconds: float = Field(ge=0)

    @model_validator(mode="after")
    def _validate_remaining_attempt(self) -> RetryDirective:
        if self.attempt >= self.max_attempts:
            raise ValueError("retry directive requires a remaining attempt")
        return self


_RESOLVED_REASONS = {
    ReasonCode.RESOLVED_WITH_EVIDENCE,
    ReasonCode.BOUNDED_AUTHORITATIVE_ANSWER,
}
_HUMAN_ACTION_REASONS = {
    ReasonCode.CUSTOMER_REQUESTED_HUMAN,
    ReasonCode.BUSINESS_APPROVAL_REQUIRED,
    ReasonCode.SENSITIVE_ACCOUNT_MUTATION_REQUIRED,
    ReasonCode.AUTOMATION_RETRY_EXHAUSTED,
    ReasonCode.POLICY_COMMITMENT_REQUIRED,
}


class DispositionDecision(FrozenModel):
    disposition: Disposition
    reply_kind: ReplyKind
    ownership_action: OwnershipAction = OwnershipAction.NONE
    reason_code: ReasonCode
    evidence_ids: tuple[str, ...] = ()
    required_customer_fields: tuple[str, ...] = ()
    retry_directive: RetryDirective | None = None

    @model_validator(mode="after")
    def _validate_consistency(self) -> DispositionDecision:
        if error := _decision_consistency_error(self):
            raise ValueError(error)
        return self


def _resolved_error(decision: DispositionDecision) -> str:
    if decision.reply_kind not in {ReplyKind.ANSWER, ReplyKind.CLOSE}:
        return "resolved disposition requires an answer or close reply"
    if decision.ownership_action is not OwnershipAction.NONE:
        return "resolved disposition cannot transfer ownership"
    if decision.reason_code not in _RESOLVED_REASONS:
        return "resolved disposition requires a resolved reason code"
    if decision.required_customer_fields or decision.retry_directive:
        return "resolved disposition cannot require customer fields or retry"
    return ""


def _awaiting_customer_error(decision: DispositionDecision) -> str:
    if decision.reply_kind is not ReplyKind.ASK:
        return "awaiting-customer disposition requires an ask reply"
    if decision.ownership_action is not OwnershipAction.NONE:
        return "customer ask cannot transfer ownership"
    if decision.reason_code is not ReasonCode.CUSTOMER_INPUT_REQUIRED:
        return "customer ask requires the customer-input reason code"
    if not decision.required_customer_fields:
        return "customer ask requires exact customer fields"
    if decision.retry_directive:
        return "customer ask cannot carry an internal retry"
    return ""


def _retry_error(decision: DispositionDecision) -> str:
    if decision.reply_kind not in {ReplyKind.PROGRESS, ReplyKind.NONE}:
        return "retry-pending disposition requires progress or no reply"
    if decision.ownership_action is not OwnershipAction.NONE:
        return "retry-pending disposition cannot transfer ownership"
    if decision.reason_code is not ReasonCode.INTERNAL_RETRY_PENDING:
        return "retry-pending disposition requires the retry reason code"
    if decision.retry_directive is None:
        return "retry-pending disposition requires a retry directive"
    if decision.required_customer_fields:
        return "internal retry cannot require customer fields"
    return ""


def _ownership_error(decision: DispositionDecision) -> str:
    expected = {
        Disposition.ENGINEERING_ESCALATION: (
            OwnershipAction.ENGINEERING,
            ReasonCode.CONFIRMED_PLATFORM_DEFECT,
            "engineering escalation requires engineering ownership",
            "engineering escalation requires a confirmed defect",
        ),
        Disposition.SRE_ESCALATION: (
            OwnershipAction.SRE,
            ReasonCode.CONFIRMED_SERVICE_INCIDENT,
            "SRE escalation requires SRE ownership",
            "SRE escalation requires a confirmed incident",
        ),
        Disposition.SECURITY_REVIEW: (
            OwnershipAction.SECURITY,
            ReasonCode.SECURITY_OR_PRIVACY_REVIEW_REQUIRED,
            "security review requires security ownership",
            "security review requires a security/privacy reason",
        ),
    }
    ownership, reason, ownership_error, reason_error = expected[decision.disposition]
    if decision.ownership_action is not ownership:
        return ownership_error
    if decision.reason_code is not reason:
        return reason_error
    return ""


def _decision_consistency_error(decision: DispositionDecision) -> str:
    if decision.disposition is Disposition.RESOLVED:
        return _resolved_error(decision)
    if decision.disposition is Disposition.AWAITING_CUSTOMER:
        return _awaiting_customer_error(decision)
    if decision.disposition is Disposition.RETRY_PENDING:
        return _retry_error(decision)
    if decision.disposition is Disposition.HUMAN_ACTION_REQUIRED:
        if decision.ownership_action not in {OwnershipAction.SUPPORT, OwnershipAction.BILLING}:
            return "human action requires support or billing ownership"
        if decision.reason_code not in _HUMAN_ACTION_REASONS:
            return "human action requires an allowed reason code"
        return ""
    return _ownership_error(decision)


class ReplyCandidate(FrozenModel):
    body: str = Field(min_length=1)
    language: str = "en"
    reply_kind: ReplyKind
    used_claim_ids: tuple[str, ...] = ()
    requested_customer_fields: tuple[str, ...] = ()
    mentions_human_followup: bool = False

    @field_validator("body")
    @classmethod
    def _normalize_body(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def _validate_candidate(self) -> ReplyCandidate:
        if self.language != "en":
            raise ValueError("public reply language must be English")
        if len(self.used_claim_ids) != len(set(self.used_claim_ids)):
            raise ValueError("reply candidate cannot repeat claim IDs")
        if len(self.requested_customer_fields) != len(
            set(self.requested_customer_fields),
        ):
            raise ValueError("reply candidate cannot repeat customer fields")
        return self


class ReplyDraft(FrozenModel):
    body: str = Field(min_length=1)
    language: str = "en"
    reply_kind: ReplyKind
    disposition: Disposition
    ownership_action: OwnershipAction
    reason_code: ReasonCode
    used_claim_ids: tuple[str, ...] = ()
    requested_customer_fields: tuple[str, ...] = ()
    mentions_human_followup: bool = False
    reviewed: bool = False
    disclosure_required: bool = True


class ReplyQualityResult(FrozenModel):
    accepted: bool
    reasons: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _validate_quality_result(self) -> ReplyQualityResult:
        if self.accepted and self.reasons:
            raise ValueError("accepted reply cannot carry rejection reasons")
        if not self.accepted and not self.reasons:
            raise ValueError("rejected reply requires reasons")
        return self
