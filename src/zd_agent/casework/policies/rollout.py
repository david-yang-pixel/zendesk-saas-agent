from __future__ import annotations

from zd_agent.casework.domain.enums import ExecutionMode


def casework_controls_ticket(
    *,
    mode: ExecutionMode,
    canary_ticket_ids: tuple[int, ...],
    ticket_id: int,
) -> bool:
    if mode is ExecutionMode.CASEWORK:
        return True
    return mode is ExecutionMode.CANARY and ticket_id in canary_ticket_ids
