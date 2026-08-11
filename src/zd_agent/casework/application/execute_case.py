from __future__ import annotations

from zd_agent.casework.domain.enums import (
    DeliveryState,
    ExecutionMode,
    OwnershipState,
    ReplyKind,
)
from zd_agent.casework.domain.models import (
    CaseworkEffectReceipt,
    CaseworkExecution,
    TicketSnapshot,
    VisualObservation,
)
from zd_agent.casework.ports.effects import CaseworkEffectPort
from zd_agent.casework.ports.orchestration import CaseworkDecisionPort


class CaseworkExecutor:
    """Coordinate one v2 decision run and its explicitly authorized effects."""

    def __init__(
        self,
        *,
        orchestrator: CaseworkDecisionPort,
        effects: CaseworkEffectPort,
    ) -> None:
        self._orchestrator = orchestrator
        self._effects = effects

    async def execute(
        self,
        *,
        ticket: TicketSnapshot,
        billing_required: bool,
        automation_attempt: int = 1,
        execution_mode: ExecutionMode,
        delivery_attempt_id: str,
        dry_run: bool,
        visual_observations: tuple[VisualObservation, ...] = (),
    ) -> CaseworkExecution:
        run = await self._orchestrator.run(
            ticket=ticket,
            billing_required=billing_required,
            automation_attempt=automation_attempt,
            visual_observations=visual_observations,
            execution_mode=execution_mode,
        )
        if run.decision.reply_kind is ReplyKind.NONE:
            receipt = CaseworkEffectReceipt(
                delivery_state=DeliveryState.NOT_REQUIRED,
                ownership_state=OwnershipState.NOT_REQUIRED,
            )
        else:
            receipt = await self._effects.apply(
                run=run,
                delivery_attempt_id=delivery_attempt_id,
                dry_run=dry_run,
            )
            if receipt.authority_projection is not None:
                run = receipt.authority_projection.project(run)
            run = run.model_copy(
                update={
                    "delivery_attempted": not dry_run,
                },
            )
            if receipt.authority_projection is not None:
                receipt = receipt.model_copy(update={"authority_projection": None})
        return CaseworkExecution(run=run, effect_receipt=receipt)
