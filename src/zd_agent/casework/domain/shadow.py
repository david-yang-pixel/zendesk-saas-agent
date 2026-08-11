from __future__ import annotations

from datetime import datetime
from hashlib import sha256
from typing import Literal, Self, TypeAlias

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
    schema_version: int = 2
    run_status: Literal["completed"] = "completed"
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
    legacy_status: str = ""
    legacy_reply_type: Literal["", "ask", "solution", "handoff", "closed"] = ""
    legacy_reply_sent: bool = False
    legacy_reply_kind: ReplyKind = ReplyKind.NONE
    legacy_ownership_action: OwnershipAction = OwnershipAction.NONE
    reply_kind_disagrees: bool = False
    ownership_disagrees: bool = False
    decision_disagrees: bool = False

    @model_validator(mode="after")
    def _validate_record(self) -> ShadowRecord:
        if self.recorded_at.tzinfo is None:
            raise ValueError("shadow record time must be timezone-aware")
        if self.execution_mode is ExecutionMode.SHADOW and self.delivery_attempted:
            raise ValueError("shadow record cannot report delivery")
        return self


class LegacyOutcomeSummary(FrozenModel):
    status: str = ""
    reply_type: Literal["", "ask", "solution", "handoff", "closed"] = ""
    reply_sent: bool = False


class ShadowFailureRecord(FrozenModel):
    schema_version: int = 2
    run_status: Literal["failed"] = "failed"
    recorded_at: datetime
    ticket_id: int = Field(gt=0)
    generation: int = Field(ge=0)
    execution_mode: ExecutionMode = ExecutionMode.SHADOW
    stage: str = Field(min_length=1, pattern=r"^[a-z0-9_]+$")
    error_type: str = Field(min_length=1)
    error_fingerprint: str = Field(min_length=20, max_length=20)
    legacy_status: str = ""
    legacy_reply_type: Literal["", "ask", "solution", "handoff", "closed"] = ""
    legacy_reply_sent: bool = False
    delivery_attempted: bool = False

    @model_validator(mode="after")
    def _validate_failure(self) -> ShadowFailureRecord:
        if self.recorded_at.tzinfo is None:
            raise ValueError("shadow failure time must be timezone-aware")
        if self.delivery_attempted:
            raise ValueError("shadow failure cannot report delivery")
        return self

    @classmethod
    def from_error(
        cls,
        *,
        recorded_at: datetime,
        ticket_id: int,
        generation: int,
        stage: str,
        error: Exception,
        legacy_status: str = "",
        legacy_reply_type: Literal["", "ask", "solution", "handoff", "closed"] = "",
        legacy_reply_sent: bool = False,
    ) -> Self:
        material = f"{type(error).__name__}\0{error}"
        return cls(
            recorded_at=recorded_at,
            ticket_id=ticket_id,
            generation=generation,
            stage=stage,
            error_type=type(error).__name__,
            error_fingerprint=_fingerprint(material),
            legacy_status=legacy_status,
            legacy_reply_type=legacy_reply_type,
            legacy_reply_sent=legacy_reply_sent,
        )


ShadowArchiveRecord: TypeAlias = ShadowRecord | ShadowFailureRecord


def _fingerprint(value: str) -> str:
    if not value:
        return ""
    return sha256(value.encode("utf-8")).hexdigest()[:20]


def _legacy_decision(
    legacy: LegacyOutcomeSummary,
) -> tuple[ReplyKind, OwnershipAction]:
    reply_kind = {
        "": ReplyKind.NONE,
        "ask": ReplyKind.ASK,
        "solution": ReplyKind.ANSWER,
        "handoff": ReplyKind.PROGRESS,
        "closed": ReplyKind.CLOSE,
    }[legacy.reply_type]
    ownership = (
        OwnershipAction.SUPPORT
        if legacy.reply_type == "handoff"
        else OwnershipAction.NONE
    )
    return reply_kind, ownership


def build_shadow_record(
    run: CaseworkRun,
    *,
    recorded_at: datetime,
    legacy: LegacyOutcomeSummary | None = None,
) -> ShadowRecord:
    billing = run.context.billing
    billing_snapshot = billing.snapshot if billing is not None else None
    reply = run.reply_draft
    legacy = legacy or LegacyOutcomeSummary()
    legacy_reply_kind, legacy_ownership = _legacy_decision(legacy)
    reply_kind_disagrees = legacy_reply_kind is not run.decision.reply_kind
    ownership_disagrees = legacy_ownership is not run.decision.ownership_action
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
        legacy_status=legacy.status,
        legacy_reply_type=legacy.reply_type,
        legacy_reply_sent=legacy.reply_sent,
        legacy_reply_kind=legacy_reply_kind,
        legacy_ownership_action=legacy_ownership,
        reply_kind_disagrees=reply_kind_disagrees,
        ownership_disagrees=ownership_disagrees,
        decision_disagrees=reply_kind_disagrees or ownership_disagrees,
    )
