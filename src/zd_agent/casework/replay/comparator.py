from __future__ import annotations

from zd_agent.casework.replay.models import (
    ReplayComparison,
    ReplayOutcome,
    ReplayScenario,
)


def compare_replay_outcome(
    *,
    scenario: ReplayScenario,
    outcome: ReplayOutcome,
) -> ReplayComparison:
    expected = scenario.expected
    mismatches: list[str] = []
    fields = (
        "tenant_id",
        "billing_plan",
        "disposition",
        "reply_kind",
        "ownership_action",
        "reason_code",
        "required_customer_fields",
    )
    for field in fields:
        actual_value = getattr(outcome, field)
        expected_value = getattr(expected, field)
        if actual_value != expected_value:
            mismatches.append(
                f"{field}: expected {expected_value!r}, got {actual_value!r}",
            )
    return ReplayComparison(
        scenario_id=scenario.scenario_id,
        matches=not mismatches,
        mismatches=tuple(mismatches),
    )
