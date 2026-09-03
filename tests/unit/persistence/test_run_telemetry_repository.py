from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import Result
from sqlalchemy.orm import Session

from casework_persistence.models import Base
from casework_persistence.repositories import RunTelemetryRepository
from contracts.outcome import Outcome, OutcomeKind
from contracts.telemetry import (
    ActiveQuestionTelemetry,
    SandboxResultTelemetry,
    TodoTelemetry,
    ToolCallTelemetry,
)


class _Gateway:
    def __init__(self, session: Session) -> None:
        self._session = session

    async def execute(self, statement: object) -> Result[Any]:
        return self._session.execute(statement)  # type: ignore[return-value]


def _outcome() -> Outcome:
    return Outcome(
        outcome_id="outcome-1",
        session_id="session-1",
        ticket_id=3726,
        kind=OutcomeKind.ANSWER,
        answer="Resolved with evidence.",
        evidence_ids=("ev-1",),
        telemetry=SandboxResultTelemetry(
            active_question=ActiveQuestionTelemetry(
                original_event_id="evt-1",
                original_request="Which workspace is affected?",
            ),
            todos=(
                TodoTelemetry(
                    task_id="todo-1",
                    goal="Find workspace",
                    status="completed",
                ),
                TodoTelemetry(
                    task_id="todo-2",
                    goal="Confirm workspace",
                    status="pending",
                ),
            ),
            tool_calls=(
                ToolCallTelemetry(
                    call_id="call-1",
                    tool_name="workspace_lookup",
                    status="succeeded",
                    summary="Found a workspace",
                    evidence_ids=("ev-1",),
                ),
            ),
            evidence_ids=("ev-1",),
        ),
    )


@pytest.mark.asyncio
async def test_run_telemetry_repository_persists_bounded_projection_and_is_idempotent(
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'telemetry.sqlite'}")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        repository = RunTelemetryRepository(_Gateway(session))
        outcome = _outcome()
        row = await repository.record_finalization(
            job_id="job-1",
            session_id="session-1",
            ticket_id=3726,
            generation=1,
            outcome=outcome,
            terminal_status="completed",
        )
        duplicate = await repository.record_finalization(
            job_id="job-1",
            session_id="session-1",
            ticket_id=3726,
            generation=1,
            outcome=outcome,
            terminal_status="completed",
        )
        session.commit()

        assert row is duplicate
        stored = await repository.get(session_id="session-1")
        assert stored is not None
        assert stored.active_question is not None
        assert stored.active_question["original_request"] == (
            "Which workspace is affected?"
        )
        assert stored.todo_total == 2
        assert stored.todo_completed == 1
        assert stored.tool_calls == 1
        assert stored.evidence_count == 1
        assert stored.evidence_ids == ["ev-1"]
        assert stored.outcome["answer"] == "Resolved with evidence."
        assert "data" not in stored.outcome
        events = await repository.list_events(session_id="session-1")
        assert [event.event_type for event in events] == ["run.finalized"]


@pytest.mark.asyncio
async def test_run_telemetry_without_result_stays_unknown(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'empty-telemetry.sqlite'}")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        repository = RunTelemetryRepository(_Gateway(session))
        outcome = Outcome(
            outcome_id="outcome-failed",
            session_id="session-failed",
            ticket_id=3727,
            kind=OutcomeKind.FAILED,
            reason="sandbox failed",
        )
        await repository.record_finalization(
            job_id="job-2",
            session_id="session-failed",
            ticket_id=3727,
            generation=1,
            outcome=outcome,
            terminal_status="failed",
        )
        stored = await repository.get(session_id="session-failed")
        assert stored is not None
        assert stored.todo_total is None
        assert stored.tool_calls is None
        assert stored.evidence_count is None
