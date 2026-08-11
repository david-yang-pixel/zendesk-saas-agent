from __future__ import annotations

from datetime import datetime

from pydantic import Field, field_validator, model_validator

from zd_agent.casework.domain.base import FrozenModel
from zd_agent.casework.domain.enums import (
    AdapterErrorCode,
    BillingBindingStatus,
    LookupStatus,
    ResolutionMethod,
    ResolutionStatus,
)


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
    error_code: AdapterErrorCode | None = None
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
        if self.status is ResolutionStatus.LOOKUP_FAILED and self.error_code is None:
            raise ValueError("failed identity lookup requires an error code")
        if self.status is not ResolutionStatus.LOOKUP_FAILED and self.error_code is not None:
            raise ValueError("identity error code is only valid for a failed lookup")
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
            facts = (
                self.plan,
                self.subscription_status,
                self.interval,
                self.stripe_status,
                self.entitlement_status,
                self.cancellation_status,
                self.renewal_status,
            )
            if any(facts):
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
    automation_attempt: int = Field(default=1, ge=1)
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
