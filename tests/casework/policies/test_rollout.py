from zd_agent.casework.domain.enums import ExecutionMode
from zd_agent.casework.policies.rollout import casework_controls_ticket


def test_casework_rollout_is_explicit_and_ticket_scoped() -> None:
    assert casework_controls_ticket(
        mode=ExecutionMode.CASEWORK,
        canary_ticket_ids=(),
        ticket_id=4001,
    )
    assert casework_controls_ticket(
        mode=ExecutionMode.CANARY,
        canary_ticket_ids=(4001,),
        ticket_id=4001,
    )
    assert not casework_controls_ticket(
        mode=ExecutionMode.CANARY,
        canary_ticket_ids=(4001,),
        ticket_id=4002,
    )
    assert not casework_controls_ticket(
        mode=ExecutionMode.SHADOW,
        canary_ticket_ids=(4001,),
        ticket_id=4001,
    )
