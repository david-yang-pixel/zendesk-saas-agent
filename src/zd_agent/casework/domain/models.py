from __future__ import annotations

from datetime import datetime

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    field_validator,
    model_validator,
)

from zd_agent.casework.domain.enums import (
    Disposition,
    EvidenceAuthority,
    EvidenceResultStatus,
    OwnershipAction,
    ReasonCode,
    ReplyKind,
    ResolutionMethod,
    ResolutionStatus,
)


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class TargetReference(FrozenModel):
    kind: str = Field(min_length=1)
    value: str = Field(min_length=1)


class WorkspaceCandidate(FrozenModel):
    tenant_id: str = Field(min_length=1)
    workspace_name: str = ""
    role: str = ""


class IdentityResolution(FrozenModel):
    contact_email: str = ""
    target_identity: str = ""
    status: ResolutionStatus
    method: ResolutionMethod = ResolutionMethod.NONE
    candidates: tuple[WorkspaceCandidate, ...] = ()
    selected_tenant_id: str = ""
    selected_workspace_name: str = ""
    selected_role: str = ""
    evidence_ids: tuple[str, ...] = ()
    detail: str = ""

    @field_validator("contact_email", "target_identity")
    @classmethod
    def _normalize_email_like_identity(cls, value: str) -> str:
        normalized = " ".join(value.split()).strip()
        return normalized.casefold() if "@" in normalized else normalized

    @model_validator(mode="after")
    def _validate_resolution(self) -> IdentityResolution:
        if self.status is ResolutionStatus.RESOLVED:
            if not self.selected_tenant_id:
                raise ValueError("resolved identity requires a selected tenant")
            candidate_ids = {candidate.tenant_id for candidate in self.candidates}
            if self.selected_tenant_id not in candidate_ids:
                raise ValueError("selected tenant must be one of the resolved candidates")
            if self.method is ResolutionMethod.NONE:
                raise ValueError("resolved identity requires a resolution method")
            if not self.evidence_ids:
                raise ValueError("resolved identity requires evidence")
        elif self.selected_tenant_id:
            raise ValueError("unresolved identity cannot select a tenant")
        return self


class BillingSnapshot(FrozenModel):
    tenant_id: str = Field(min_length=1)
    identity_tenant_id: str = Field(min_length=1)
    plan: str = ""
    subscription_status: str = ""
    interval: str = ""
    stripe_status: str = ""
    entitlement_status: str = ""
    cancellation_status: str = ""
    renewal_status: str = ""
    observed_at: datetime
    evidence_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _validate_target_binding(self) -> BillingSnapshot:
        if self.tenant_id != self.identity_tenant_id:
            raise ValueError("billing tenant must match the resolved target tenant")
        if self.observed_at.tzinfo is None:
            raise ValueError("billing observation time must be timezone-aware")
        if not self.evidence_ids:
            raise ValueError("billing snapshot requires evidence")
        return self


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
        if (
            self.time_window_start
            and self.time_window_end
            and self.time_window_start > self.time_window_end
        ):
            raise ValueError("evidence time-window start must not exceed its end")
        return self

    def target_value(self, kind: str) -> str | None:
        return next((target.value for target in self.targets if target.kind == kind), None)


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
