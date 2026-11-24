from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from httpx import ASGITransport, AsyncClient
import pytest
from sqlalchemy import create_engine, insert, update
from sqlalchemy.engine import Result
from sqlalchemy.orm import Session

from casework_admin import api_v2
from casework_admin.app import create_app
from casework_admin.postgres_reads import PostgresAdminReadRepository
from casework_persistence.models import Base, CaseJobRow, OutboxMessage, PublicDelivery, TicketState
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
    assert all(run.config_revision == "cfg-1" for run in runs.items)
    assert all(run.question is None for run in runs.items)
    assert all(run.todo_completed is None for run in runs.items)
    assert all(run.todo_total is None for run in runs.items)
    assert all(run.tool_calls is None for run in runs.items)
    assert all(run.evidence is None for run in runs.items)
    assert all(run.checkpoint is None for run in runs.items)
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
            expected_event_id="messaging:evt-1",
            config=FROZEN,
            job_id="job-3726",
            session_id="session-3726",
            available_at=NOW,
        )
    reads = PostgresAdminReadRepository(transaction_scope=sessions.transaction)

    assert (await reads.runs(query="3726")).total == 1
    assert (await reads.runs(query="missing")).total == 0
    assert (await reads.runs(status="running")).total == 1
    run = await reads.run(session_id="session-3726")
    assert run is not None
    assert run.event_id == "evt-1"
    assert run.expected_event_id == "messaging:evt-1"
    assert run.attempts == 0


@pytest.mark.asyncio
async def test_admin_routes_show_current_ticket_coverage_without_run_telemetry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    sessions = _Sessions(tmp_path / "admin-coverage.sqlite")
    latest_at = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=12)
    private_message = "Private follow-up from the event payload"
    monkeypatch.setattr(api_v2, "_credential_value", lambda _name: "")
    async with sessions.transaction() as gateway:
        events = EventRepository(gateway)
        jobs = JobRepository(gateway)
        for ticket_id, job_id in (
            (3726, "job-old"), (3726, "job-queued"),
            (3727, "job-answered"), (3728, "job-historical"),
        ):
            event_id = f"event-{job_id}"
            await events.record(
                event_id=event_id,
                ticket_id=ticket_id,
                occurred_at=latest_at,
                source="zendesk",
                customer_origin="customer",
                payload={"body": private_message},
            )
            await jobs.enqueue(
                ticket_id=ticket_id, event_id=event_id, config=FROZEN,
                job_id=job_id, session_id=f"session-{job_id}",
            )
            if job_id != "job-queued":
                await gateway.execute(
                    update(CaseJobRow).where(CaseJobRow.job_id == job_id)
                    .values(status="completed", finished_at=latest_at)
                )
                await gateway.execute(insert(OutboxMessage).values(
                    outbox_id=f"outbox-{job_id}", job_id=job_id, ticket_id=ticket_id,
                    generation=1, snapshot_cursor=event_id, locale="en",
                    body="Previously delivered reply", idempotency_key=job_id, status="sent",
                ))
        delivery_id = "54812148-0e8f-468c-9dde-da77cb8528da"
        await gateway.execute(insert(PublicDelivery).values(
            job_id="job-answered", ticket_id=3727, delivery_id=delivery_id, status="uncertain",
            messages=[], reply_body="Private candidate draft", started_at=latest_at,
        ))
        await gateway.execute(update(CaseJobRow).where(CaseJobRow.job_id == "job-answered").values(status="failed"))
        await gateway.execute(
            update(TicketState).where(TicketState.ticket_id == 3726).values(
                latest_customer_seq=8, answered_customer_seq=7, latest_customer_at=latest_at,
                answered_by_job_id="job-old", human_cancelled_at=latest_at,
                last_event_id="event-job-queued",
            )
        )
        await gateway.execute(
            update(TicketState).where(TicketState.ticket_id == 3727).values(
                latest_customer_seq=3, answered_customer_seq=3, latest_customer_at=latest_at,
                answered_by_job_id="job-answered",
            )
        )

    reads = PostgresAdminReadRepository(transaction_scope=sessions.transaction)
    transport = ASGITransport(app=create_app(read_repository=reads))
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        traces = await client.get("/api/traces")
        assert traces.status_code == 200
        by_job = {item["job_id"]: item for item in traces.json()["items"]}
        assert by_job["job-old"]["status"] == "completed"
        assert by_job["job-old"]["delivery_status"] == "sent"
        assert by_job["job-queued"]["has_trace"] is False
        assert by_job["job-answered"]["delivery_status"] == "uncertain"
        receipt = await client.get(f"/api/deliveries/{delivery_id}")
        assert receipt.status_code == 200
        assert receipt.json()["status"] == "uncertain"
        assert receipt.json()["job_id"] == "job-answered"
        assert "Private candidate draft" not in receipt.text
        assert (await client.post("/api/traces/job-answered/retry")).status_code == 409
        resolution = f"/api/deliveries/{delivery_id}/staff-resolution"
        for _ in range(2):
            assert (await client.post(resolution, json={"message_id": "zendesk:201"})).json() == {"status": "staff_handled"}
        assert (await client.post(resolution, json={"message_id": "zendesk:other"})).status_code == 409
        disposition = (await client.get(f"/api/deliveries/{delivery_id}")).json()
        assert disposition["confirmed_at"] is None
        assert disposition["status"] == "staff_handled"
        assert (await client.post("/api/traces/job-answered/retry")).status_code == 409
        assert by_job["job-old"]["ticket_coverage"] == by_job["job-queued"]["ticket_coverage"]

        old_trace = await client.get("/api/traces/3726", params={"job_id": "job-old"})
        assert old_trace.status_code == 200
        assert old_trace.json()["delivery"]["status"] == "sent"

        runs = await client.get("/api/runs", params={"query": "3726"})
        assert runs.status_code == 200
        assert runs.json()["total"] == 2
        queued_run = await client.get("/api/runs/session-job-queued")
        assert queued_run.status_code == 200
        assert queued_run.json()["tool_calls"] is None

        for response in (traces, old_trace, runs, queued_run):
            assert private_message not in response.text

    for coverage in (
        by_job["job-old"]["ticket_coverage"], old_trace.json()["ticket_coverage"],
        queued_run.json()["ticket_coverage"],
        *(item["ticket_coverage"] for item in runs.json()["items"]),
    ):
        assert coverage["status"] == "pending"
        assert coverage["latest_customer_seq"] == 8
        assert coverage["answered_customer_seq"] == 7
        assert coverage["answered_by_job_id"] == "job-old"
        assert coverage["human_takeover"] is True
        assert datetime.fromisoformat(coverage["human_cancelled_at"]) == latest_at
        assert datetime.fromisoformat(coverage["latest_customer_at"]) == latest_at
        assert 720 <= coverage["pending_age_seconds"] <= (datetime.now(UTC) - latest_at).total_seconds()

    answered = by_job["job-answered"]["ticket_coverage"]
    assert answered["status"] == "answered"
    assert answered["latest_customer_seq"] == answered["answered_customer_seq"] == 3
    assert answered["answered_by_job_id"] == "job-answered"
    assert answered["pending_age_seconds"] is None
    assert answered["human_takeover"] is False

    historical = by_job["job-historical"]
    assert historical["delivery_status"] == "sent"
    assert historical["ticket_coverage"]["status"] == "unknown"
    assert historical["ticket_coverage"]["latest_customer_seq"] is None
    assert historical["ticket_coverage"]["pending_age_seconds"] is None
    assert historical["ticket_coverage"]["answered_by_job_id"] is None
