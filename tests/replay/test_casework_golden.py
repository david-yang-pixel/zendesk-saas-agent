from __future__ import annotations

import json
from pathlib import Path

from zd_agent.casework.replay.comparator import compare_replay_outcome
from zd_agent.casework.replay.models import ReplayScenario
from zd_agent.casework.replay.runner import run_replay


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "casework_golden.json"


async def test_all_redacted_golden_cases_match_expected_casework_outcome() -> None:
    payload = json.loads(FIXTURE_PATH.read_text())
    scenarios = tuple(ReplayScenario.model_validate(item) for item in payload)

    comparisons = []
    for scenario in scenarios:
        comparisons.append(
            compare_replay_outcome(
                scenario=scenario,
                outcome=await run_replay(scenario),
            ),
        )

    assert {item.scenario_id for item in comparisons} == {
        "ticket-3798-quota",
        "ticket-3802-target-email",
        "ticket-3821-refund-approval",
        "ticket-3846-exact-workspace-name",
        "ticket-3861-no-published-timeline",
        "transient-identity-timeout",
        "confirmed-platform-defect",
    }
    failures = {
        item.scenario_id: item.mismatches
        for item in comparisons
        if not item.matches
    }
    assert failures == {}


def test_golden_fixtures_are_redacted() -> None:
    raw = FIXTURE_PATH.read_text().casefold()

    assert "@dify.ai" not in raw
    assert "hubstaff" not in raw
    assert "palfish" not in raw
    assert "api_key" not in raw
    assert "token" not in raw
