from __future__ import annotations

from pydantic import Field, model_validator

from zd_agent.casework.domain.base import FrozenModel
from zd_agent.casework.domain.decision import DispositionDecision, ReplyDraft
from zd_agent.casework.domain.enums import (
    DeliveryState,
    OwnershipAction,
    OwnershipState,
    ReplyKind,
)
from zd_agent.casework.domain.run import CaseworkRun


class EffectAuthorityProjection(FrozenModel):
    """Minimal frozen fields needed to replay one coherent casework effect."""

    generation: int = Field(ge=0)
    source_event_id: str = Field(min_length=1)
    snapshot_customer_message_count: int | None = Field(default=None, ge=0)
    snapshot_customer_last_message_id: str = Field(min_length=1)
    ownership_action: OwnershipAction
    decision: DispositionDecision
    reply: ReplyDraft

    @classmethod
    def from_run(
        cls,
        run: CaseworkRun,
        *,
        snapshot_customer_message_count: int | None,
        snapshot_customer_last_message_id: str,
    ) -> EffectAuthorityProjection:
        reply = run.reply_draft
        if reply is None:
            raise ValueError("effect authority requires a customer-visible reply")
        return cls(
            generation=run.context.ticket.generation,
            source_event_id=run.context.ticket.latest_event_id,
            snapshot_customer_message_count=snapshot_customer_message_count,
            snapshot_customer_last_message_id=snapshot_customer_last_message_id,
            ownership_action=run.decision.ownership_action,
            decision=run.decision,
            reply=reply,
        )

    def project(self, run: CaseworkRun) -> CaseworkRun:
        """Keep current context/evidence while restoring frozen effect authority."""
        return run.model_copy(
            update={"decision": self.decision, "reply_draft": self.reply},
        )

    @model_validator(mode="after")
    def _validate_projection(self) -> EffectAuthorityProjection:
        if self.ownership_action is not self.decision.ownership_action:
            raise ValueError("authority ownership must match its decision")
        reply = self.reply
        decision = self.decision
        if (
            reply.reply_kind is not decision.reply_kind
            or reply.disposition is not decision.disposition
            or reply.ownership_action is not decision.ownership_action
            or reply.reason_code is not decision.reason_code
        ):
            raise ValueError("authority reply must match its decision")
        return self


class CaseworkEffectReceipt(FrozenModel):
    delivery_state: DeliveryState
    ownership_state: OwnershipState
    delivery_reference: str = ""
    ownership_reference: str = ""
    accepted_snapshot_count: int | None = None
    accepted_snapshot_last_id: str = ""
    detail: str = ""
    # Frozen audit text from the authoritative effect row. This avoids
    # rendering a hybrid audit if a retry produced newer context after the
    # customer-visible effect had already been frozen.
    authoritative_audit: str = ""
    # Transient authority used when a retry encounters an older frozen effect.
    # The executor projects it onto the current run and clears it before the
    # execution object is persisted or returned.
    authority_projection: EffectAuthorityProjection | None = None

    @model_validator(mode="after")
    def _validate_references(self) -> CaseworkEffectReceipt:
        if (
            self.delivery_state is DeliveryState.CONFIRMED
            and not self.delivery_reference
        ):
            raise ValueError("confirmed delivery requires a durable reference")
        if (
            self.ownership_state is OwnershipState.CONFIRMED
            and not self.ownership_reference
        ):
            raise ValueError("confirmed ownership requires a durable reference")
        return self


class CaseworkExecution(FrozenModel):
    run: CaseworkRun
    effect_receipt: CaseworkEffectReceipt

    @model_validator(mode="after")
    def _validate_effects(self) -> CaseworkExecution:
        decision = self.run.decision
        receipt = self.effect_receipt
        if decision.reply_kind is ReplyKind.NONE:
            if receipt.delivery_state is not DeliveryState.NOT_REQUIRED:
                raise ValueError("no-reply decision cannot produce a delivery")
        if decision.ownership_action is OwnershipAction.NONE:
            if receipt.ownership_state is not OwnershipState.NOT_REQUIRED:
                raise ValueError(
                    "no-ownership decision cannot produce ownership effects"
                )
        elif receipt.ownership_state is OwnershipState.NOT_REQUIRED:
            raise ValueError("ownership decision requires a persisted ownership effect")
        return self
