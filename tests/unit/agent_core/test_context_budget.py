import pytest

from casework_agent_core.context_budget import CompactionLevel, ContextBudget


@pytest.mark.parametrize(
    ("used_tokens", "expected"),
    [
        (59_999, CompactionLevel.NONE),
        (60_000, CompactionLevel.SOFT),
        (79_999, CompactionLevel.SOFT),
        (80_000, CompactionLevel.HARD),
    ],
)
def test_compaction_thresholds_are_deterministic(
    used_tokens: int,
    expected: CompactionLevel,
) -> None:
    budget = ContextBudget(window_tokens=100_000)

    assert budget.level_for(used_tokens) is expected


def test_context_usage_cannot_exceed_the_model_window() -> None:
    budget = ContextBudget(window_tokens=100_000)

    with pytest.raises(ValueError, match="exceeds context window"):
        budget.level_for(100_001)


def test_context_window_must_be_positive() -> None:
    with pytest.raises(ValueError, match="positive"):
        ContextBudget(window_tokens=0)
