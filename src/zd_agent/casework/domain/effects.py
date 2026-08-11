from __future__ import annotations

from pydantic import model_validator

from zd_agent.casework.domain.base import FrozenModel
from zd_agent.casework.domain.enums import (
    DeliveryState,
    ExecutionMode,
    OwnershipAction,
    OwnershipState,
    ReplyKind,
)
from zd_agent.casework.domain.run import CaseworkRun


class CaseworkEffectReceipt(FrozenModel):
    delivery_state: DeliveryState
    ownership_state: OwnershipState
    delivery_reference: str = ""
    ownership_reference: str = ""
    accepted_snapshot_count: int | None = None
    accepted_snapshot_last_id: str = ""
    detail: str = ""

    @model_validator(mode="after")
    def _validate_references(self) -> CaseworkEffectReceipt:
        if self.delivery_state is DeliveryState.CONFIRMED and not self.delivery_reference:
            raise ValueError("confirmed delivery requires a durable reference")
        if self.ownership_state is OwnershipState.CONFIRMED and not self.ownership_reference:
            raise ValueError("confirmed ownership requires a durable reference")
        return self


class CaseworkExecution(FrozenModel):
    run: CaseworkRun
    effect_receipt: CaseworkEffectReceipt

    @model_validator(mode="after")
    def _validate_effects(self) -> CaseworkExecution:
        decision = self.run.decision
        receipt = self.effect_receipt
        if self.run.execution_mode is ExecutionMode.SHADOW:
            if receipt.delivery_state is not DeliveryState.NOT_ATTEMPTED:
                raise ValueError("shadow execution cannot produce delivery effects")
            if receipt.ownership_state is not OwnershipState.NOT_REQUIRED:
                raise ValueError("shadow execution cannot produce ownership effects")
        if decision.reply_kind is ReplyKind.NONE:
            if receipt.delivery_state is not DeliveryState.NOT_REQUIRED:
                raise ValueError("no-reply decision cannot produce a delivery")
        if decision.ownership_action is OwnershipAction.NONE:
            if receipt.ownership_state is not OwnershipState.NOT_REQUIRED:
                raise ValueError("no-ownership decision cannot produce ownership effects")
        elif receipt.ownership_state is OwnershipState.NOT_REQUIRED:
            raise ValueError("ownership decision requires a persisted ownership effect")
        return self
