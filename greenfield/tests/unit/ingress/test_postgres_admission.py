from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.engine import Result
from sqlalchemy.orm import Session

from casework_ingress.ports import AdmissionEvent
from casework_ingress.postgres_admission import PostgresAdmission
from casework_ingress.processing_rules import NormalizedZendeskEvent
from casework_persistence.models import Base, CaseJobRow, TicketState
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


def _event(*, event_id: str, message: str = "please investigate") -> AdmissionEvent:
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
        raw_payload={"event_id": event_id, "ticket_id": 3726},
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

    first = await admission.admit(_event(event_id="evt-1"))
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
