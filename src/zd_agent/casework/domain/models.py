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
    AdapterErrorCode,
    AnswerStatus,
    BillingBindingStatus,
    ClaimRejectionCode,
    ClaimRole,
    ClaimStatus,
    Disposition,
    EvidenceAuthority,
    EvidenceResultStatus,
    LookupStatus,
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


class FormIdentity(FrozenModel):
    email: str = ""
    workspace_id: str = ""


class AttachmentRef(FrozenModel):
    attachment_id: str = Field(min_length=1)
    filename: str = Field(min_length=1)
    content_type: str = ""
    download_url: str = ""


class VisualObservation(FrozenModel):
    attachment_id: str = Field(min_length=1)
    evidence_id: str = Field(min_length=1)
    visible_text: str = ""
    ui_state: str = ""
    uncertainty: str = ""
    untrusted: bool = True

    @model_validator(mode="after")
    def _require_untrusted_boundary(self) -> VisualObservation:
        if not self.untrusted:
            raise ValueError("visual observations must remain untrusted evidence")
        return self


class TicketSnapshot(FrozenModel):
    ticket_id: int = Field(gt=0)
    generation: int = Field(ge=0)
    status: str = Field(min_length=1)
    channel: str = ""
    locale: str = ""
    latest_event_id: str = Field(min_length=1)
    latest_event_at: datetime
    questions: tuple[str, ...] = Field(min_length=1)
    form_identity: FormIdentity = Field(default_factory=FormIdentity)
    requester_email: str = ""
    attachments: tuple[AttachmentRef, ...] = ()

    @model_validator(mode="after")
    def _validate_snapshot(self) -> TicketSnapshot:
        if self.latest_event_at.tzinfo is None:
            raise ValueError("ticket event time must be timezone-aware")
        if any(not question.strip() for question in self.questions):
            raise ValueError("ticket questions cannot be blank")
        attachment_ids = [attachment.attachment_id for attachment in self.attachments]
        if len(attachment_ids) != len(set(attachment_ids)):
            raise ValueError("ticket snapshot cannot contain duplicate attachments")
        return self


class WorkspaceCandidate(FrozenModel):
    tenant_id: str = Field(min_length=1)
    workspace_name: str = ""
    role: str = ""


class WorkspaceMembershipLookup(FrozenModel):
    email: str = Field(min_length=3)
    status: LookupStatus
    candidates: tuple[WorkspaceCandidate, ...] = ()
    evidence_id: str = ""
    error_code: AdapterErrorCode | None = None

    @field_validator("email")
    @classmethod
    def _normalize_email(cls, value: str) -> str:
        return value.strip().casefold()

    @model_validator(mode="after")
    def _validate_lookup(self) -> WorkspaceMembershipLookup:
        candidate_ids = [candidate.tenant_id for candidate in self.candidates]
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError("workspace lookup cannot contain duplicate tenant IDs")
        if self.status is LookupStatus.SUCCESS:
            if not self.evidence_id:
                raise ValueError("successful workspace lookup requires evidence")
            if self.error_code is not None:
                raise ValueError("successful workspace lookup cannot carry an error")
        elif self.status is LookupStatus.FAILED:
            if self.error_code is None:
                raise ValueError("failed workspace lookup requires an error code")
            if self.candidates:
                raise ValueError("failed workspace lookup cannot carry candidates")
        elif self.candidates or self.error_code is not None:
            raise ValueError("pending workspace lookup cannot carry results")
        return self


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


class BillingLookup(FrozenModel):
    tenant_id: str = Field(min_length=1)
    status: LookupStatus
    observed_at: datetime
    evidence_id: str = ""
    error_code: AdapterErrorCode | None = None
    plan: str = ""
    subscription_status: str = ""
    interval: str = ""
    stripe_status: str = ""
    entitlement_status: str = ""
    cancellation_status: str = ""
    renewal_status: str = ""

    @model_validator(mode="after")
    def _validate_lookup(self) -> BillingLookup:
        if self.observed_at.tzinfo is None:
            raise ValueError("billing lookup time must be timezone-aware")
        if self.status is LookupStatus.SUCCESS:
            if not self.evidence_id:
                raise ValueError("successful billing lookup requires evidence")
            if self.error_code is not None:
                raise ValueError("successful billing lookup cannot carry an error")
        elif self.status is LookupStatus.FAILED:
            if self.error_code is None:
                raise ValueError("failed billing lookup requires an error code")
            if any(
                (
                    self.plan,
                    self.subscription_status,
                    self.interval,
                    self.stripe_status,
                    self.entitlement_status,
                    self.cancellation_status,
                    self.renewal_status,
                ),
            ):
                raise ValueError("failed billing lookup cannot carry subscription facts")
        elif self.error_code is not None:
            raise ValueError("pending billing lookup cannot carry an error")
        return self


class BillingBinding(FrozenModel):
    status: BillingBindingStatus
    snapshot: BillingSnapshot | None = None
    error_code: AdapterErrorCode | None = None
    evidence_ids: tuple[str, ...] = ()
    detail: str = ""

    @model_validator(mode="after")
    def _validate_binding(self) -> BillingBinding:
        if self.status is BillingBindingStatus.RESOLVED:
            if self.snapshot is None:
                raise ValueError("resolved billing binding requires a snapshot")
            if self.error_code is not None:
                raise ValueError("resolved billing binding cannot carry an error")
        elif self.snapshot is not None:
            raise ValueError("unresolved billing binding cannot carry a snapshot")
        if self.status is BillingBindingStatus.LOOKUP_FAILED and self.error_code is None:
            raise ValueError("failed billing binding requires an error code")
        return self


class CaseContext(FrozenModel):
    ticket: TicketSnapshot
    built_at: datetime
    identity: IdentityResolution
    billing_required: bool = False
    billing: BillingBinding | None = None
    visual_observations: tuple[VisualObservation, ...] = ()
    warnings: tuple[str, ...] = ()
    customer_required_fields: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _validate_context(self) -> CaseContext:
        if self.built_at.tzinfo is None:
            raise ValueError("case context build time must be timezone-aware")
        if self.billing_required and self.billing is None:
            raise ValueError("billing-required context needs a billing binding")
        if not self.billing_required and self.billing is not None:
            raise ValueError("non-billing context cannot carry a billing binding")
        attachment_ids = {
            attachment.attachment_id for attachment in self.ticket.attachments
        }
        if any(
            observation.attachment_id not in attachment_ids
            for observation in self.visual_observations
        ):
            raise ValueError("visual observation must belong to a ticket attachment")
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
        return next((target.value for target in self.targets if target.kind == kind), None)


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
        if self.status is ClaimStatus.CONTRADICTED and not self.contradicting_evidence_ids:
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
