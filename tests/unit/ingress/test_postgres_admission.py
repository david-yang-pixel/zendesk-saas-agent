from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import replace
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
from casework_persistence.models import (
    Base,
    CaseJobRow,
    EventRecord,
    HumanHandoffRow,
    TicketState,
)
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

NOW = datetime(2026, 9, 1, tzinfo=UTC)


def _event(
    *,
    event_id: str,
    message: str = "please investigate",
    snapshot_cursor: str | None = None,
    occurred_at: datetime | None = None,
    received_at: datetime = NOW,
) -> AdmissionEvent:
    return AdmissionEvent(
        event_id=event_id,
        received_at=received_at,
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
            "message_created_at": occurred_at.isoformat() if occurred_at else None,
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

    operator_mention = _operator_mention("evt-operator-mention")
    mention_result = await admission.admit(operator_mention)
    assert mention_result.action == "enqueue"
    async with sessions.transaction() as session:
        state = (await session.execute(select(TicketState))).scalar_one()
        assert state.human_cancelled_at is None
        assert state.human_resumed_at == (NOW + timedelta(seconds=1)).replace(tzinfo=None)
        assert state.human_resume_cursor == "mention:evt-operator-mention"


def _operator_mention(
    event_id: str, *, occurred_at: datetime | None = NOW + timedelta(seconds=1),
    received_at: datetime = NOW + timedelta(seconds=2), comment_id: str | None = None,
) -> AdmissionEvent:
    return AdmissionEvent(
        event_id=event_id,
        received_at=received_at,
        normalized=NormalizedZendeskEvent(
            ticket_id=3726,
            tags=("saas",),
            assignee="saas-support",
            status="open",
            is_customer_message=False,
            is_staff_note=True,
            message_text="@agent please look again",
        ),
        raw_payload={
            "event_id": event_id, "ticket_id": 3726,
            "snapshot_cursor": f"mention:{comment_id or event_id}",
            "message_created_at": occurred_at.isoformat() if occurred_at else None,
        },
    )


@pytest.mark.asyncio
async def test_human_command_records_handoff_and_acknowledges_once_per_takeover(
    tmp_path: Path,
) -> None:
    sessions = _SessionAdapter(tmp_path / "handoff.sqlite")
    async with sessions.transaction() as session:
        await ConfigRevisionRepository(session).bootstrap(
            revision="cfg-1", config=CONFIG, actor="bootstrap"
        )
    admission = PostgresAdmission(transaction_scope=sessions.transaction)

    first = await admission.admit(_event(event_id="evt-human-1", message=" Human! "))
    repeat = await admission.admit(_event(event_id="evt-human-2", message="真人"))
    replay = await admission.admit(_event(event_id="evt-human-1", message="human"))
    await admission.admit(_operator_mention("evt-mention"))
    after_mention = await admission.admit(_event(
        event_id="evt-human-3", message="human", occurred_at=NOW + timedelta(seconds=2),
    ))

    assert [first.action, repeat.action, replay.action, after_mention.action] == [
        "cancel",
        "cancel",
        "duplicate",
        "cancel",
    ]
    async with sessions.transaction() as session:
        rows = (
            (await session.execute(select(HumanHandoffRow).order_by(HumanHandoffRow.handoff_id)))
            .scalars()
            .all()
        )
    assert [(row.handoff_id, row.acknowledge) for row in rows] == [
        ("evt-human-1", True),
        ("evt-human-2", False),
        ("evt-human-3", True),
    ]
    assert all(row.ticket_id == 3726 and row.status == "pending" for row in rows)
    assert rows[0].customer_message == "Human!"
    assert rows[0].available_at.replace(tzinfo=UTC) == datetime(2026, 9, 1, tzinfo=UTC)


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


@pytest.mark.asyncio
async def test_contact_form_queues_once_and_coalesces_with_questions_and_attachments(tmp_path):
    sessions = _SessionAdapter(tmp_path / "form.sqlite")
    async with sessions.transaction() as session:
        await ConfigRevisionRepository(session).bootstrap(revision="cfg-1", config=CONFIG, actor="test")
    admission = PostgresAdmission(transaction_scope=sessions.transaction)
    form = "Name: Test\nEmail: test@example.invalid\nPlan: Pro"
    submission = _event(event_id="form", message=form, snapshot_cursor="messaging:form-id")
    assert (await admission.admit(submission)).action == "enqueue"
    assert (await admission.admit(submission)).action == "duplicate"
    async with sessions.transaction() as session:
        job = (await session.execute(select(CaseJobRow))).scalar_one()
        assert job.expected_event_id == "messaging:form-id"
        assert job.available_at.replace(tzinfo=UTC) == NOW + timedelta(seconds=20)
    assert (await admission.admit(_event(event_id="full-text", message=f"{form}\nWhy was I charged?"))).action == "enqueue"
    attachment = _event(event_id="attachment", message="", snapshot_cursor="messaging:attachment-id")
    assert (await admission.admit(replace(attachment, raw_payload={
        **attachment.raw_payload, "has_attachments": True,
    }))).action == "enqueue"
    async with sessions.transaction() as session:
        state = (await session.execute(select(TicketState))).scalar_one()
        assert state.latest_customer_seq == 3
        assert state.answered_customer_seq == 0
        assert state.latest_customer_at is not None
        rows = (await session.execute(select(EventRecord))).scalars().all()
        assert {row.event_id: row.customer_seq for row in rows} == {"form": 1, "full-text": 2, "attachment": 3}
        assert (await session.execute(select(CaseJobRow))).scalar_one().expected_event_id == "messaging:attachment-id"


@pytest.mark.asyncio
async def test_exact_late_webhook_is_covered_without_erasing_operator_or_customer_jobs(tmp_path):
    sessions = _SessionAdapter(tmp_path / "covered.sqlite")
    async with sessions.transaction() as session:
        await ConfigRevisionRepository(session).bootstrap(revision="cfg-1", config=CONFIG, actor="test")
    admission = PostgresAdmission(transaction_scope=sessions.transaction)
    assert (await admission.admit(_event(event_id="first", snapshot_cursor="messaging:first"))).action == "enqueue"
    async with sessions.transaction() as session:
        first = (await session.execute(select(EventRecord))).scalar_one()
        first.answered_by_job_id = "delivered-job"
    await admission.admit(_operator_mention("mention-a"))
    await admission.admit(_operator_mention("mention-b"))
    late = await admission.admit(_event(event_id="repeat-delivery", snapshot_cursor="zen:sunco:conversation_message:first"))
    assert late.reason == "customer_message_covered"
    assert (await admission.admit(_event(event_id="newer", snapshot_cursor="messaging:newer"))).action == "enqueue"
    async with sessions.transaction() as session:
        state = (await session.execute(select(TicketState))).scalar_one()
        assert state.latest_customer_seq == 2
        jobs = (await session.execute(select(CaseJobRow))).scalars().all()
        assert {(job.event_id, job.is_operator_mention) for job in jobs} == {
            ("first", False), ("mention-a", True), ("mention-b", True),
        }
        assert next(job for job in jobs if not job.is_operator_mention).expected_event_id == "messaging:newer"


@pytest.mark.asyncio
async def test_admission_orders_takeover_and_resume_by_provider_time(tmp_path):
    sessions = _SessionAdapter(tmp_path / "ordered.sqlite")
    async with sessions.transaction() as session:
        await ConfigRevisionRepository(session).bootstrap(revision="cfg-1", config=CONFIG, actor="test")
    admission = PostgresAdmission(transaction_scope=sessions.transaction)
    old = NOW + timedelta(seconds=1)
    resume = NOW + timedelta(seconds=2)
    new = NOW + timedelta(seconds=3)
    late_delivery = NOW + timedelta(seconds=5)

    assert (await admission.admit(_event(event_id="first-human", message="human", occurred_at=old))).action == "cancel"
    assert (await admission.admit(_operator_mention("resume", occurred_at=resume, comment_id="12"))).action == "enqueue"
    stale = await admission.admit(_event(
        event_id="late-human", message="human", occurred_at=old, received_at=late_delivery,
    ))
    assert (stale.action, stale.reason) == ("ignore", "stale_human_request")
    assert (await admission.admit(_event(
        event_id="current-human", message="human", occurred_at=new, snapshot_cursor="messaging:request-3",
    ))).action == "cancel"
    assert (await admission.admit(_event(
        event_id="second-delivery", message="human", received_at=late_delivery, snapshot_cursor="messaging:request-3",
    ))).action == "duplicate"

    same_note = await admission.admit(_operator_mention("resume-replay", comment_id="12", received_at=late_delivery))
    assert same_note.action == "duplicate"
    for stale_at in (old, new, None):
        different_note = await admission.admit(_operator_mention(
            f"stale-{stale_at}", occurred_at=stale_at, received_at=late_delivery,
        ))
        assert (different_note.action, different_note.reason) == ("ignore", "stale_operator_mention")

    async with sessions.transaction() as session:
        state = (await session.execute(select(TicketState))).scalar_one()
        assert state.human_cancelled_at == new.replace(tzinfo=None)
        assert state.human_resumed_at == resume.replace(tzinfo=None)
        assert state.human_resume_cursor == "mention:12"
        assert state.generation == 3
        assert len((await session.execute(select(HumanHandoffRow))).scalars().all()) == 2
