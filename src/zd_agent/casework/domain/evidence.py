from __future__ import annotations

from datetime import datetime

from pydantic import Field, JsonValue, model_validator

from zd_agent.casework.domain.base import FrozenModel, TargetReference
from zd_agent.casework.domain.enums import (
    ActionRequirement,
    AdapterErrorCode,
    AnswerStatus,
    ClaimRejectionCode,
    ClaimRole,
    ClaimStatus,
    EvidenceAuthority,
    EvidenceResultStatus,
    OwnershipAction,
    ReasonCode,
)


class EvidenceItem(FrozenModel):
    evidence_id: str = Field(min_length=1)
    call_id: str = ""
    source: str = Field(min_length=1)
    authority: EvidenceAuthority
    result_status: EvidenceResultStatus
    targets: tuple[TargetReference, ...] = ()
    observed_at: datetime
    time_window_start: datetime | None = None
    time_window_end: datetime | None = None
    fresh_until: datetime | None = None
    supports_roles: tuple[ClaimRole, ...] = ()
    payload: JsonValue | None = None
    payload_ref: str = ""
    redacted: bool = True

    @model_validator(mode="after")
    def _validate_observation(self) -> EvidenceItem:
        if self.observed_at.tzinfo is None:
            raise ValueError("evidence observation time must be timezone-aware")
        if self.result_status is EvidenceResultStatus.SUCCESS and not self.call_id:
            raise ValueError("successful evidence requires a tool call ID")
        if self.payload is None and not self.payload_ref:
            raise ValueError("evidence requires a payload or durable payload reference")
        if self.time_window_start and self.time_window_start.tzinfo is None:
            raise ValueError("evidence time-window start must be timezone-aware")
        if self.time_window_end and self.time_window_end.tzinfo is None:
            raise ValueError("evidence time-window end must be timezone-aware")
        if self.fresh_until and self.fresh_until.tzinfo is None:
            raise ValueError("evidence freshness time must be timezone-aware")
        if (
            self.time_window_start
            and self.time_window_end
            and self.time_window_start > self.time_window_end
        ):
            raise ValueError("evidence time-window start must not exceed its end")
        return self

    def target_value(self, kind: str) -> str | None:
        return next(
            (target.value for target in self.targets if target.kind == kind), None
        )


class Claim(FrozenModel):
    claim_id: str = Field(min_length=1)
    statement: str = Field(min_length=1)
    role: ClaimRole
    status: ClaimStatus
    material: bool = True
    supporting_evidence_ids: tuple[str, ...] = ()
    contradicting_evidence_ids: tuple[str, ...] = ()
    targets: tuple[TargetReference, ...] = ()
    public_allowed: bool = False

    @model_validator(mode="after")
    def _validate_claim(self) -> Claim:
        if (
            self.material
            and self.status in {ClaimStatus.CONFIRMED, ClaimStatus.EVIDENCE_SUPPORTED}
            and not self.supporting_evidence_ids
        ):
            raise ValueError("supported material claim requires evidence IDs")
        if (
            self.status is ClaimStatus.CONTRADICTED
            and not self.contradicting_evidence_ids
        ):
            raise ValueError("contradicted claim requires contradicting evidence IDs")
        return self


class ClaimAdmission(FrozenModel):
    claim_id: str = Field(min_length=1)
    accepted: bool
    rejection_code: ClaimRejectionCode = ClaimRejectionCode.NONE
    eligible_evidence_ids: tuple[str, ...] = ()
    detail: str = ""

    @model_validator(mode="after")
    def _validate_admission(self) -> ClaimAdmission:
        if self.accepted and self.rejection_code is not ClaimRejectionCode.NONE:
            raise ValueError("accepted claim cannot carry a rejection code")
        if not self.accepted and self.rejection_code is ClaimRejectionCode.NONE:
            raise ValueError("rejected claim requires a rejection code")
        return self


_RETRYABLE_ERROR_CODES = {
    AdapterErrorCode.TIMEOUT,
    AdapterErrorCode.TRANSPORT_UNAVAILABLE,
    AdapterErrorCode.RATE_LIMITED,
    AdapterErrorCode.AUTH_EXPIRED,
    AdapterErrorCode.MALFORMED_RESPONSE,
}


class AdapterFailure(FrozenModel):
    operation: str = Field(min_length=1)
    code: AdapterErrorCode
    attempt: int = Field(ge=1)
    max_attempts: int = Field(ge=1)
    retry_after_seconds: float = Field(default=0, ge=0)
    evidence_id: str = ""
    detail: str = ""

    @model_validator(mode="after")
    def _validate_attempts(self) -> AdapterFailure:
        if self.attempt > self.max_attempts:
            raise ValueError("adapter failure attempt cannot exceed max attempts")
        return self

    def can_retry(self) -> bool:
        return self.code in _RETRYABLE_ERROR_CODES and self.attempt < self.max_attempts

    def retry_exhausted(self) -> bool:
        return self.code in _RETRYABLE_ERROR_CODES and self.attempt >= self.max_attempts


class HumanActionRequest(FrozenModel):
    ownership_action: OwnershipAction
    reason_code: ReasonCode
    summary: str = Field(min_length=1)
    customer_impact: str = Field(min_length=1)
    completed_checks: tuple[str, ...] = Field(min_length=1)
    unresolved_action: str = Field(min_length=1)
    acceptance_criteria: tuple[str, ...] = Field(min_length=1)
    evidence_ids: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_ownership(self) -> HumanActionRequest:
        if self.ownership_action is OwnershipAction.NONE:
            raise ValueError("human action request requires a target team")
        return self


class ActionProposal(FrozenModel):
    """Model-observed action need without authority to select an owner."""

    requirement: ActionRequirement
    summary: str = Field(min_length=1)
    customer_impact: str = Field(min_length=1)
    completed_checks: tuple[str, ...] = Field(min_length=1)
    unresolved_action: str = Field(min_length=1)
    acceptance_criteria: tuple[str, ...] = Field(min_length=1)
    evidence_ids: tuple[str, ...] = Field(min_length=1)


class CaseAssessment(FrozenModel):
    question: str = Field(min_length=1)
    answer_status: AnswerStatus
    admitted_claims: tuple[Claim, ...] = ()
    customer_only_fields: tuple[str, ...] = ()
    blocking_failure: AdapterFailure | None = None
    human_action: HumanActionRequest | None = None
    evidence_ids: tuple[str, ...] = ()
    close_after_reply: bool = False

    @model_validator(mode="after")
    def _validate_answer(self) -> CaseAssessment:
        if self.answer_status in {AnswerStatus.SUPPORTED, AnswerStatus.BOUNDED}:
            if not self.admitted_claims:
                raise ValueError("answerable assessment requires admitted claims")
            if self.customer_only_fields:
                raise ValueError("answerable assessment cannot require customer fields")
        if any(not claim.public_allowed for claim in self.admitted_claims):
            raise ValueError("assessment cannot admit internal-only claims")
        return self


class AssessmentProposal(FrozenModel):
    question: str = Field(min_length=1)
    answer_status: AnswerStatus
    proposed_claims: tuple[Claim, ...] = ()
    proposed_customer_fields: tuple[str, ...] = ()
    blocking_failure: AdapterFailure | None = None
    action_proposal: ActionProposal | None = None
    evidence_ids: tuple[str, ...] = ()
    close_after_reply: bool = False


class AssessmentBuild(FrozenModel):
    assessment: CaseAssessment
    admissions: tuple[ClaimAdmission, ...] = ()
    rejected_customer_fields: tuple[str, ...] = ()
    rejected_action_reason: str = ""


class InvestigationResult(FrozenModel):
    proposal: AssessmentProposal
    evidence: tuple[EvidenceItem, ...] = ()
