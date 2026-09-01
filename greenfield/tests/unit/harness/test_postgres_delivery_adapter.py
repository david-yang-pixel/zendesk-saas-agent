from __future__ import annotations

from collections.abc import AsyncGenerator, Iterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import Result
from sqlalchemy.orm import Session

from casework_harness.delivery import (
    DeliveryTransactionScope,
    PostgresDeliveryAdapter,
)
from casework_persistence.models import (
    Base,
    EventRecord,
    OutboxMessage,
    TicketState,
)
from casework_persistence.repositories import SessionPort

NOW = datetime(2026, 9, 1, tzinfo=UTC)
OUTBOX_KEY = (
    "ticket:3726:generation:7:cursor-sha256:"
    "037c955d26b63d2cfeab0a1ae1f414d40badebaab61d55099e66111cd03afc0c"
)


class AsyncSessionShim:
    def __init__(self, session: Session) -> None:
        self._session = session

    async def execute(self, statement: Any) -> Result[Any]:
        return self._session.execute(statement)


@pytest.fixture
def db_session() -> Iterator[Session]:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def transaction_scope(session: Session) -> DeliveryTransactionScope:
    shim = AsyncSessionShim(session)

    @asynccontextmanager
    async def scope() -> AsyncGenerator[SessionPort, None]:
        yield cast(SessionPort, shim)

    return scope


def seed_delivery_state(session: Session) -> None:
    session.add(TicketState(ticket_id=3726, generation=7))
    session.add(
        EventRecord(
            event_id="event-01",
            ticket_id=3726,
            occurred_at=NOW,
            source="zendesk",
            customer_origin="customer",
            event_type="ticket.updated",
            payload={"snapshot_cursor": "cursor-01"},
            received_at=NOW,
        )
    )
    session.add(
        OutboxMessage(
            outbox_id=OUTBOX_KEY,
            job_id=None,
            ticket_id=3726,
            generation=7,
            snapshot_cursor="cursor-01",
            locale="ja-JP",
            body="reply",
            idempotency_key=OUTBOX_KEY,
            status="pending",
            attempt_count=0,
            created_at=NOW,
            updated_at=NOW,
        )
    )
    session.commit()


@pytest.mark.asyncio
async def test_postgres_adapter_reads_latest_cursor_and_claims_pending_row(
    db_session: Session,
) -> None:
    seed_delivery_state(db_session)
    adapter = PostgresDeliveryAdapter(
        transaction_scope=transaction_scope(db_session)
    )

    current = await adapter.latest(3726)
    claimed = await adapter.claim(
        key=OUTBOX_KEY,
        ticket_id=3726,
        generation=7,
        cursor="cursor-01",
    )

    assert current.generation == 7
    assert current.cursor == "cursor-01"
    assert claimed is True
    row = db_session.get(OutboxMessage, OUTBOX_KEY)
    assert row is not None
    assert row.status == "attempted"
    assert row.attempt_count == 1


@pytest.mark.asyncio
async def test_postgres_adapter_rejects_new_customer_cursor_and_human_generation(
    db_session: Session,
) -> None:
    seed_delivery_state(db_session)
    adapter = PostgresDeliveryAdapter(
        transaction_scope=transaction_scope(db_session)
    )

    db_session.add(
        EventRecord(
            event_id="event-02",
            ticket_id=3726,
            occurred_at=NOW + timedelta(seconds=1),
            source="zendesk",
            customer_origin="customer",
            event_type="ticket.updated",
            payload={"snapshot_cursor": "cursor-02"},
            received_at=NOW + timedelta(seconds=1),
        )
    )
    db_session.commit()

    assert not await adapter.claim(
        key=OUTBOX_KEY,
        ticket_id=3726,
        generation=7,
        cursor="cursor-01",
    )
    state = db_session.get(TicketState, 3726)
    assert state is not None
    state.generation = 8
    db_session.commit()

    assert not await adapter.claim(
        key=OUTBOX_KEY,
        ticket_id=3726,
        generation=7,
        cursor="cursor-01",
    )
    row = db_session.get(
        OutboxMessage,
        OUTBOX_KEY,
    )
    assert row is not None
    assert row.status == "pending"
