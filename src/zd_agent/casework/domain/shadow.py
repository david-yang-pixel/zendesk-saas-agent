from __future__ import annotations

from datetime import datetime
from hashlib import sha256

from pydantic import Field, model_validator

from zd_agent.casework.domain.base import FrozenModel
from zd_agent.casework.domain.enums import (
    BillingBindingStatus,
    Disposition,
    ExecutionMode,
    OwnershipAction,
    ReasonCode,
    ReplyKind,
    ResolutionMethod,
    ResolutionStatus,
)
from zd_agent.casework.domain.run import CaseworkRun


class ShadowRecord(FrozenModel):
    schema_version: int = 1
    recorded_at: datetime
    ticket_id: int = Field(gt=0)
    generation: int = Field(ge=0)
    execution_mode: ExecutionMode
    identity_status: ResolutionStatus
    identity_method: ResolutionMethod
    target_fingerprint: str = ""
    billing_status: BillingBindingStatus | None = None
    billing_plan: str = ""
    subscription_status: str = ""
    disposition: Disposition
    reply_kind: ReplyKind
    ownership_action: OwnershipAction
    reason_code: ReasonCode
    claim_ids: tuple[str, ...] = ()
    requested_customer_fields: tuple[str, ...] = ()
    reply_fingerprint: str = ""
    delivery_attempted: bool = False

    @model_validator(mode="after")
    def _validate_record(self) -> ShadowRecord:
        if self.recorded_at.tzinfo is None:
            raise ValueError("shadow record time must be timezone-aware")
        if self.execution_mode is ExecutionMode.SHADOW and self.delivery_attempted:
            raise ValueError("shadow record cannot report delivery")
        return self


def _fingerprint(value: str) -> str:
    if not value:
        return ""
    return sha256(value.encode("utf-8")).hexdigest()[:20]


def build_shadow_record(
    run: CaseworkRun,
    *,
    recorded_at: datetime,
) -> ShadowRecord:
    billing = run.context.billing
    billing_snapshot = billing.snapshot if billing is not None else None
    reply = run.reply_draft
    return ShadowRecord(
        recorded_at=recorded_at,
        ticket_id=run.context.ticket.ticket_id,
        generation=run.context.ticket.generation,
        execution_mode=run.execution_mode,
        identity_status=run.context.identity.status,
        identity_method=run.context.identity.method,
        target_fingerprint=_fingerprint(run.context.identity.selected_tenant_id),
        billing_status=billing.status if billing is not None else None,
        billing_plan=billing_snapshot.plan if billing_snapshot is not None else "",
        subscription_status=(
            billing_snapshot.subscription_status
            if billing_snapshot is not None
            else ""
        ),
        disposition=run.decision.disposition,
        reply_kind=run.decision.reply_kind,
        ownership_action=run.decision.ownership_action,
        reason_code=run.decision.reason_code,
        claim_ids=tuple(
            claim.claim_id
            for claim in run.assessment_build.assessment.admitted_claims
        ),
        requested_customer_fields=run.decision.required_customer_fields,
        reply_fingerprint=_fingerprint(reply.body if reply is not None else ""),
        delivery_attempted=run.delivery_attempted,
    )
