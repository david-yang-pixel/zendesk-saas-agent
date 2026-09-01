from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import Result
from sqlalchemy.orm import Session

from casework_admin.postgres_reads import PostgresAdminReadRepository
from casework_persistence.models import Base
from casework_persistence.repositories import (
    ConfigSnapshot,
    EventRepository,
    JobRepository,
    RunTelemetryRepository,
)
from contracts.outcome import Outcome, OutcomeKind
from contracts.telemetry import (
    ActiveQuestionTelemetry,
    ContextCheckpointTelemetry,
    SandboxLifecycleEvent,
    SandboxResultTelemetry,
    TodoTelemetry,
    ToolCallTelemetry,
)


class _Gateway:
    def __init__(self, session: Session) -> None:
        self._session = session

    async def execute(self, statement: object) -> Result[Any]:
        return self._session.execute(statement)  # type: ignore[return-value]


class _Sessions:
    def __init__(self, path: Path) -> None:
        self._engine = create_engine(f"sqlite:///{path}")
        Base.metadata.create_all(self._engine)

    async def __aenter__(self) -> _Gateway:
        self._session = Session(self._engine, expire_on_commit=False)
        return _Gateway(self._session)

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        if exc_type is None:
            self._session.commit()
        else:
            self._session.rollback()
        self._session.close()

    def transaction(self) -> "_Transaction":
        return _Transaction(self)


class _Transaction:
    def __init__(self, owner: _Sessions) -> None:
        self._owner = owner

    async def __aenter__(self) -> _Gateway:
        return await self._owner.__aenter__()

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        await self._owner.__aexit__(exc_type, exc, tb)


@pytest.mark.asyncio
async def test_admin_reads_versioned_run_telemetry(tmp_path: Path) -> None:
    sessions = _Sessions(tmp_path / "admin-telemetry.sqlite")
    async with sessions.transaction() as gateway:
        await EventRepository(gateway).record(
            event_id="evt-1",
            ticket_id=3726,
            occurred_at=datetime(2026, 9, 1, tzinfo=UTC),
            source="zendesk",
            customer_origin="customer",
        )
        job = await JobRepository(gateway).enqueue(
            ticket_id=3726,
            event_id="evt-1",
            config=ConfigSnapshot(
                config_revision="cfg-1",
                root_model_config_id="root",
                child_model_config_ids=(),
                model_digest="model",
                skill_digest="skill",
                tool_digest="tool",
            ),
            job_id="job-1",
            session_id="session-1",
        )
        assert job.job_id == "job-1"
        outcome = Outcome(
            outcome_id="outcome-1",
            session_id="session-1",
            ticket_id=3726,
            kind=OutcomeKind.ANSWER,
            answer="Resolved",
            evidence_ids=("ev-1",),
            telemetry=SandboxResultTelemetry(
                active_question=ActiveQuestionTelemetry(
                    original_event_id="evt-1",
                    original_request="Where is the workspace?",
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
                        summary="Found evidence",
                        evidence_ids=("ev-1",),
                    ),
                ),
                evidence_ids=("ev-1",),
                context_checkpoint=ContextCheckpointTelemetry(
                    checkpoint_ref="checkpoint-session-1",
                    level="none",
                    used_tokens=10,
                    exact_identifiers=("evt-1",),
                ),
                sandbox_lifecycle=(
                    SandboxLifecycleEvent(
                        state="destroyed",
                        occurred_at=datetime(2026, 9, 1, tzinfo=UTC),
                        sandbox_id="sandbox-1",
                        attempt=1,
                    ),
                ),
            ),
        )
        await RunTelemetryRepository(gateway).record_finalization(
            job_id="job-1",
            session_id="session-1",
            ticket_id=3726,
            generation=1,
            outcome=outcome,
            terminal_status="completed",
        )

    reads = PostgresAdminReadRepository(transaction_scope=sessions.transaction)
    record = await reads.run(session_id="session-1")
    assert record is not None
    assert record.active_question is not None
    assert record.active_question.original_request == "Where is the workspace?"
    assert record.todo_total == 1
    assert record.todos[0].status == "completed"
    assert record.tool_calls == 1
    assert record.tool_call_records[0].summary == "Found evidence"
    assert record.evidence_ids == ("ev-1",)
    assert record.context_checkpoint is not None
    assert record.context_checkpoint.checkpoint_ref == "checkpoint-session-1"
    assert record.sandbox_lifecycle is not None
    assert record.sandbox_lifecycle.events[0].state == "destroyed"
