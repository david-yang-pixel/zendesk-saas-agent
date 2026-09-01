from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from contracts.outcome import Outcome, OutcomeKind
from contracts.telemetry import (
    ActiveQuestionTelemetry,
    ContextCheckpointTelemetry,
    SandboxLifecycleEvent,
    SandboxResultTelemetry,
    TodoTelemetry,
    ToolCallTelemetry,
)


def _telemetry() -> SandboxResultTelemetry:
    return SandboxResultTelemetry(
        active_question=ActiveQuestionTelemetry(
            original_event_id="evt-1",
            original_request="Where is the workspace?",
            clarification_event_ids=(),
        ),
        todos=(
            TodoTelemetry(
                task_id="todo-1",
                goal="Find workspace",
                status="completed",
            ),
        ),
        tool_calls=(
            ToolCallTelemetry(
                call_id="call-1",
                tool_name="zendesk_ticket",
                status="succeeded",
                summary="Found workspace evidence",
                evidence_ids=("ev-1",),
            ),
        ),
        evidence_ids=("ev-1",),
        context_checkpoint=ContextCheckpointTelemetry(
            checkpoint_ref="checkpoint-session-1",
            level="none",
            used_tokens=32,
            exact_identifiers=("evt-1",),
            retained_rounds=(),
            summary=None,
        ),
        sandbox_lifecycle=(
            SandboxLifecycleEvent(
                state="creating",
                occurred_at=datetime(2026, 9, 1, tzinfo=UTC),
                sandbox_id="sandbox-1",
                attempt=1,
            ),
            SandboxLifecycleEvent(
                state="destroyed",
                occurred_at=datetime(2026, 9, 1, 0, 1, tzinfo=UTC),
                sandbox_id="sandbox-1",
                attempt=1,
            ),
        ),
    )


def test_telemetry_is_versioned_and_contains_only_bounded_summaries() -> None:
    telemetry = _telemetry()
    assert telemetry.version == "sandbox-result-telemetry.v1"
    assert telemetry.tool_calls[0].evidence_ids == ("ev-1",)
    assert "data" not in telemetry.model_dump(mode="json")

    outcome = Outcome(
        outcome_id="outcome-1",
        session_id="session-1",
        ticket_id=1,
        kind=OutcomeKind.ANSWER,
        answer="Resolved",
        evidence_ids=("ev-1",),
        telemetry=telemetry,
    )
    assert outcome.telemetry == telemetry


def test_telemetry_rejects_large_summary_and_uncontrolled_artifact_payload() -> None:
    with pytest.raises(ValidationError):
        ToolCallTelemetry(
            call_id="call-1",
            tool_name="tool",
            status="succeeded",
            summary="x" * 2049,
        )

    with pytest.raises(ValidationError):
        SandboxResultTelemetry.model_validate(
            {
                "artifact_refs": [{"ref": "artifact://a", "data": "raw"}],
            }
        )
