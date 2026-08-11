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
        if self.disposition is Disposition.RESOLVED:
            if self.reply_kind not in {ReplyKind.ANSWER, ReplyKind.CLOSE}:
                raise ValueError("resolved disposition requires an answer or close reply")
            if self.ownership_action is not OwnershipAction.NONE:
                raise ValueError("resolved disposition cannot transfer ownership")
            if self.reason_code not in _RESOLVED_REASONS:
                raise ValueError("resolved disposition requires a resolved reason code")
            if self.required_customer_fields or self.retry_directive:
                raise ValueError("resolved disposition cannot require customer fields or retry")
        elif self.disposition is Disposition.AWAITING_CUSTOMER:
            if self.reply_kind is not ReplyKind.ASK:
                raise ValueError("awaiting-customer disposition requires an ask reply")
            if self.ownership_action is not OwnershipAction.NONE:
                raise ValueError("customer ask cannot transfer ownership")
            if self.reason_code is not ReasonCode.CUSTOMER_INPUT_REQUIRED:
                raise ValueError("customer ask requires the customer-input reason code")
            if not self.required_customer_fields:
                raise ValueError("customer ask requires exact customer fields")
            if self.retry_directive:
                raise ValueError("customer ask cannot carry an internal retry")
        elif self.disposition is Disposition.RETRY_PENDING:
            if self.reply_kind not in {ReplyKind.PROGRESS, ReplyKind.NONE}:
                raise ValueError("retry-pending disposition requires progress or no reply")
            if self.ownership_action is not OwnershipAction.NONE:
                raise ValueError("retry-pending disposition cannot transfer ownership")
            if self.reason_code is not ReasonCode.INTERNAL_RETRY_PENDING:
                raise ValueError("retry-pending disposition requires the retry reason code")
            if self.retry_directive is None:
                raise ValueError("retry-pending disposition requires a retry directive")
            if self.required_customer_fields:
                raise ValueError("internal retry cannot require customer fields")
        elif self.disposition is Disposition.HUMAN_ACTION_REQUIRED:
            if self.ownership_action not in {OwnershipAction.SUPPORT, OwnershipAction.BILLING}:
                raise ValueError("human action requires support or billing ownership")
            if self.reason_code not in _HUMAN_ACTION_REASONS:
                raise ValueError("human action requires an allowed reason code")
        elif self.disposition is Disposition.ENGINEERING_ESCALATION:
            if self.ownership_action is not OwnershipAction.ENGINEERING:
                raise ValueError("engineering escalation requires engineering ownership")
            if self.reason_code is not ReasonCode.CONFIRMED_PLATFORM_DEFECT:
                raise ValueError("engineering escalation requires a confirmed defect")
        elif self.disposition is Disposition.SRE_ESCALATION:
            if self.ownership_action is not OwnershipAction.SRE:
                raise ValueError("SRE escalation requires SRE ownership")
            if self.reason_code is not ReasonCode.CONFIRMED_SERVICE_INCIDENT:
                raise ValueError("SRE escalation requires a confirmed incident")
        elif self.disposition is Disposition.SECURITY_REVIEW:
            if self.ownership_action is not OwnershipAction.SECURITY:
                raise ValueError("security review requires security ownership")
            if self.reason_code is not ReasonCode.SECURITY_OR_PRIVACY_REVIEW_REQUIRED:
                raise ValueError("security review requires a security/privacy reason")
        return self


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
