from __future__ import annotations

from typing import Protocol

from zd_agent.casework.domain.enums import ExecutionMode
from zd_agent.casework.domain.models import (
    CaseworkRun,
    TicketSnapshot,
    VisualObservation,
)


class CaseworkDecisionPort(Protocol):
    async def run(
        self,
        *,
        ticket: TicketSnapshot,
        billing_required: bool,
        automation_attempt: int = 1,
        visual_observations: tuple[VisualObservation, ...] = (),
        execution_mode: ExecutionMode = ExecutionMode.CASEWORK,
    ) -> CaseworkRun: ...
