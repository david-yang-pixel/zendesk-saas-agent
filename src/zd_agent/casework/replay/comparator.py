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
        "admitted_claim_ids",
    )
    for field in fields:
        actual_value = getattr(outcome, field)
        expected_value = getattr(expected, field)
        if actual_value != expected_value:
            mismatches.append(
                f"{field}: expected {expected_value!r}, got {actual_value!r}",
            )
    forbidden_fields = set(expected.forbidden_customer_fields)
    if invalid_fields := forbidden_fields & set(outcome.required_customer_fields):
        mismatches.append(
            "forbidden customer fields: " + ", ".join(sorted(invalid_fields)),
        )
    reply = outcome.reply_body.casefold()
    for phrase in expected.required_reply_phrases:
        if phrase.casefold() not in reply:
            mismatches.append(f"missing required reply phrase: {phrase!r}")
    for phrase in expected.forbidden_reply_phrases:
        if phrase.casefold() in reply:
            mismatches.append(f"forbidden reply phrase: {phrase!r}")
    return ReplayComparison(
        scenario_id=scenario.scenario_id,
        matches=not mismatches,
        mismatches=tuple(mismatches),
    )
