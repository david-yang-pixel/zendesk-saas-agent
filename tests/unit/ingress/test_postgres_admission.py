from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.engine import Result
from sqlalchemy.orm import Session

from casework_ingress.ports import AdmissionEvent, AdmissionUnavailable
from casework_ingress.postgres_admission import PostgresAdmission
from casework_ingress.processing_rules import NormalizedZendeskEvent
from casework_persistence.models import Base, CaseJobRow, TicketState, EventRecord
from casework_persistence.repositories import ConfigRevisionRepository


class _SessionAdapter:
    def __init__(self, database_path: Path) -> None:
        self._engine = create_engine(f"sqlite:///{database_path}")
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


class _Gateway:
    def __init__(self, session: Session) -> None:
        self._session = session

    async def execute(self, statement: Any) -> Result[Any]:
        return self._session.execute(statement)


CONFIG: dict[str, object] = {
    "desired_state": "running",
    "root_model": {
        "provider": "openai",
        "model": "gpt-5.6-sol",
        "credential_ref": "secret/root",
    },
    "subagent_slots": [
        {
            "slot_id": "logs",
            "enabled": True,
            "provider": "openai",
            "model": "gpt-5.6-luna",
            "credential_ref": "secret/logs",
            "max_parallel": 1,
        }
    ],
    "rules": {
        "saas_tag": "saas",
        "saas_assignee": "saas-support",
        "statuses": ["new", "open"],
        "human_aliases": ["真人"],
        "reply_enabled": False,
        "default_locale": "en",
        "footers": {},
    },
    "enabled_skills": ["zendesk-casework"],
    "enabled_tools": ["zendesk_ticket"],
}


def _event(
    *,
    event_id: str,
    message: str = "please investigate",
    snapshot_cursor: str | None = None,
) -> AdmissionEvent:
    return AdmissionEvent(
        event_id=event_id,
        received_at=datetime(2026, 9, 1, tzinfo=UTC),
        normalized=NormalizedZendeskEvent(
            ticket_id=3726,
            tags=("saas",),
            assignee="saas-support",
            status="open",
            is_customer_message=True,
            message_text=message,
        ),
        raw_payload={
            "event_id": event_id,
            "ticket_id": 3726,
            **(
                {"snapshot_cursor": snapshot_cursor}
                if snapshot_cursor is not None
                else {}
            ),
        },
    )


@pytest.mark.asyncio
async def test_postgres_admission_atomically_enqueues_and_deduplicates(
    tmp_path: Path,
) -> None:
    sessions = _SessionAdapter(tmp_path / "admission.sqlite")
    async with sessions.transaction() as session:
        await ConfigRevisionRepository(session).bootstrap(
            revision="cfg-1", config=CONFIG, actor="bootstrap"
        )
    admission = PostgresAdmission(transaction_scope=sessions.transaction)

    first = await admission.admit(
        _event(event_id="evt-1", snapshot_cursor="messaging:customer-1")
    )
    duplicate = await admission.admit(_event(event_id="evt-1"))

    assert first.action == "enqueue"
    assert duplicate.action == "duplicate"
    async with sessions.transaction() as session:
        jobs = (await session.execute(select(CaseJobRow))).scalars().all()
    assert len(jobs) == 1
    assert jobs[0].ticket_id == 3726
    assert jobs[0].generation == 1
    assert jobs[0].config_revision == "cfg-1"
    assert jobs[0].child_model_config_ids == ["logs"]
    assert jobs[0].event_id == "evt-1"
    assert jobs[0].expected_event_id == "messaging:customer-1"
    # SQLite drops timezone information; production PostgreSQL retains it.
    assert jobs[0].available_at.replace(tzinfo=UTC) == (
        datetime(2026, 9, 1, tzinfo=UTC) + timedelta(seconds=20)
    )


@pytest.mark.asyncio
async def test_postgres_admission_human_command_fences_active_job(
    tmp_path: Path,
) -> None:
    sessions = _SessionAdapter(tmp_path / "human.sqlite")
    async with sessions.transaction() as session:
        await ConfigRevisionRepository(session).bootstrap(
            revision="cfg-1", config=CONFIG, actor="bootstrap"
        )
    admission = PostgresAdmission(transaction_scope=sessions.transaction)
    await admission.admit(_event(event_id="evt-1"))

    result = await admission.admit(_event(event_id="evt-human", message="human"))

    assert result.action == "cancel"
    async with sessions.transaction() as session:
        job = (await session.execute(select(CaseJobRow))).scalar_one()
        state = (await session.execute(select(TicketState))).scalar_one()
        assert job.status == "cancelled"
        assert state.generation == 2
        assert state.human_cancelled_at is not None

    subsequent = await admission.admit(_event(event_id="evt-after-human", message="Why 401?"))
    assert subsequent.action == "ignore"
    assert subsequent.reason == "human_takeover_active"

    # Verify that an internal note @David Yang actively triggers and clears human takeover restriction
    operator_mention = AdmissionEvent(
        event_id="evt-operator-mention",
        received_at=datetime(2026, 9, 1, tzinfo=UTC),
        normalized=NormalizedZendeskEvent(
            ticket_id=3726,
            tags=("saas",),
            assignee="saas-support",
            status="open",
            is_customer_message=False,
            message_text="@David Yang please analyze this customer issue",
        ),
        raw_payload={"event_id": "evt-operator-mention", "ticket_id": 3726},
    )
    mention_result = await admission.admit(operator_mention)
    assert mention_result.action == "enqueue"
    async with sessions.transaction() as session:
        state = (await session.execute(select(TicketState))).scalar_one()
        assert state.human_cancelled_at is None


@pytest.mark.asyncio
async def test_webhook_mention_uses_one_event_and_replay_preserves_later_human_lock(tmp_path):
    from casework_ingress.ticket_events import parse_ticket_event
    from tests.unit.ingress.test_ticket_events_webhook import payload

    sessions = _SessionAdapter(tmp_path / "mention.sqlite")
    async with sessions.transaction() as session:
        await ConfigRevisionRepository(session).bootstrap(revision="cfg-1", config=CONFIG, actor="test")
    parsed = parse_ticket_event(payload())
    assert parsed is not None
    event = AdmissionEvent(event_id=parsed.event_id, received_at=datetime.now(UTC),
                           normalized=parsed.normalized(), raw_payload=parsed.payload())
    admission = PostgresAdmission(transaction_scope=sessions.transaction)
    assert (await admission.admit(event)).action == "enqueue"
    async with sessions.transaction() as session:
        state = (await session.execute(select(TicketState))).scalar_one()
        state.human_cancelled_at = datetime(2026, 9, 23, tzinfo=UTC)
    assert (await admission.admit(event)).action == "duplicate"
    async with sessions.transaction() as session:
        assert len((await session.execute(select(EventRecord))).scalars().all()) == 1
        job = (await session.execute(select(CaseJobRow))).scalar_one()
        assert job.event_id == parsed.event_id
        assert job.expected_event_id == "mention:1234"
        assert job.available_at.replace(tzinfo=UTC) == event.received_at
        state = (await session.execute(select(TicketState))).scalar_one()
        assert state.human_cancelled_at is not None


@pytest.mark.asyncio
async def test_database_failure_is_sanitized():
    from sqlalchemy.exc import OperationalError

    @asynccontextmanager
    async def unavailable():
        raise OperationalError("sensitive query", {}, Exception("private"))
        yield  # pragma: no cover

    admission = PostgresAdmission(transaction_scope=unavailable)
    with pytest.raises(AdmissionUnavailable, match=r"^postgres_unavailable$"):
        await admission.admit(_event(event_id="failure"))
