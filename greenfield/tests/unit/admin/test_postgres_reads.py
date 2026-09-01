from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
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
    ConfigRevisionRepository,
    ConfigSnapshot,
    EventRepository,
    JobRepository,
)

NOW = datetime(2026, 9, 1, tzinfo=UTC)
FROZEN = ConfigSnapshot(
    config_revision="cfg-1",
    root_model_config_id="root",
    child_model_config_ids=("logs",),
    model_digest="sha256:model",
    skill_digest="sha256:skills",
    tool_digest="sha256:tools",
)


class _Gateway:
    def __init__(self, session: Session) -> None:
        self._session = session

    async def execute(self, statement: Any) -> Result[Any]:
        return self._session.execute(statement)


class _Sessions:
    def __init__(self, path: Path) -> None:
        self._engine = create_engine(f"sqlite:///{path}")
        Base.metadata.create_all(self._engine)

    @asynccontextmanager
    async def transaction(self) -> AsyncGenerator[_Gateway, None]:
        with Session(self._engine, expire_on_commit=False) as session:
            gateway = _Gateway(session)
            try:
                yield gateway
            except Exception:
                session.rollback()
                raise
            else:
                session.commit()


@pytest.mark.asyncio
async def test_postgres_admin_reads_truthful_job_and_registry_projections(
    tmp_path: Path,
) -> None:
    sessions = _Sessions(tmp_path / "admin-read.sqlite")
    async with sessions.transaction() as gateway:
        await ConfigRevisionRepository(gateway).bootstrap(
            revision="cfg-1",
            actor="bootstrap",
            config={
                "desired_state": "running",
                "root_model": {
                    "provider": "openai",
                    "model": "root",
                    "credential_ref": "secret/root",
                },
                "rules": {"saas_tag": "saas", "saas_assignee": "saas-support"},
                "enabled_skills": ["core-casework"],
                "enabled_tools": ["zendesk_ticket"],
            },
        )
        events = EventRepository(gateway)
        jobs = JobRepository(gateway)
        for event_id, ticket_id in (("evt-1", 3726), ("evt-2", 3727)):
            await events.record(
                event_id=event_id,
                ticket_id=ticket_id,
                occurred_at=NOW,
                source="zendesk",
                customer_origin="customer",
            )
            await jobs.enqueue(
                ticket_id=ticket_id,
                event_id=event_id,
                config=FROZEN,
                job_id=f"job-{ticket_id}",
                session_id=f"session-{ticket_id}",
                available_at=NOW,
            )
        claimed = await jobs.claim_next(worker_id="worker", now=NOW)
        assert claimed is not None
        await jobs.finish(
            job_id=claimed.job_id,
            worker_id="worker",
            status="completed",
            now=NOW,
        )

    reads = PostgresAdminReadRepository(transaction_scope=sessions.transaction)
    overview = await reads.overview(
        desired_state="running", config_revision="cfg-1"
    )
    runs = await reads.runs()
    registry = await reads.registry()

    assert runs.total == 2
    assert {run.ticket_id for run in runs.items} == {3726, 3727}
    assert all(run.question == "" for run in runs.items)
    assert all(run.todo_total == 0 for run in runs.items)
    assert sum(item.count for item in overview.queue_health) == 2
    assert [item.id for item in registry.skills] == ["core-casework"]
    assert [item.id for item in registry.tools] == ["zendesk_ticket"]


@pytest.mark.asyncio
async def test_postgres_admin_run_filters_use_ticket_session_and_status(
    tmp_path: Path,
) -> None:
    sessions = _Sessions(tmp_path / "admin-filter.sqlite")
    async with sessions.transaction() as gateway:
        await ConfigRevisionRepository(gateway).bootstrap(
            revision="cfg-1",
            actor="bootstrap",
            config={
                "desired_state": "running",
                "root_model": {
                    "provider": "openai",
                    "model": "root",
                    "credential_ref": "secret/root",
                },
                "rules": {"saas_tag": "saas", "saas_assignee": "saas-support"},
            },
        )
        await EventRepository(gateway).record(
            event_id="evt-1",
            ticket_id=3726,
            occurred_at=NOW,
            source="zendesk",
            customer_origin="customer",
        )
        await JobRepository(gateway).enqueue(
            ticket_id=3726,
            event_id="evt-1",
            config=FROZEN,
            job_id="job-3726",
            session_id="session-3726",
            available_at=NOW,
        )
    reads = PostgresAdminReadRepository(transaction_scope=sessions.transaction)

    assert (await reads.runs(query="3726")).total == 1
    assert (await reads.runs(query="missing")).total == 0
    assert (await reads.runs(status="running")).total == 1
    assert await reads.run(session_id="session-3726") is not None
