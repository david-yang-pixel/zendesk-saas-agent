from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

import pytest
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.schema import CreateIndex, CreateTable
from sqlalchemy.sql.schema import Table

from casework_persistence.models import (
    ACTIVE_JOB_STATUSES,
    Base,
    CaseJobRow,
    EventRecord,
    OutboxMessage,
    TicketState,
)
from casework_persistence.repositories import ConfigSnapshot


def test_schema_has_durable_event_dedup_and_ticket_generation_tables() -> None:
    assert {"events", "ticket_states", "case_jobs", "outbox_messages"} <= set(
        Base.metadata.tables
    )
    event_table = cast(Table, EventRecord.__table__)
    state_table = cast(Table, TicketState.__table__)
    job_table = cast(Table, CaseJobRow.__table__)
    outbox_table = cast(Table, OutboxMessage.__table__)
    assert list(event_table.primary_key.columns.keys()) == ["event_id"]
    assert list(state_table.primary_key.columns.keys()) == ["ticket_id"]
    assert bool(job_table.c.event_id.unique)
    assert bool(outbox_table.c.idempotency_key.unique)


def test_case_job_has_postgres_partial_unique_index_for_one_active_job() -> None:
    job_table = cast(Table, CaseJobRow.__table__)
    index = next(
        index
        for index in job_table.indexes
        if str(index.name) == "uq_case_jobs_one_active_ticket"
    )
    assert [column.name for column in index.columns] == ["ticket_id"]
    compiled = str(CreateIndex(index).compile(dialect=postgresql.dialect()))
    assert "WHERE" in compiled
    for status in ACTIVE_JOB_STATUSES:
        assert status in compiled


def test_required_config_snapshot_is_explicit_and_immutable() -> None:
    snapshot = ConfigSnapshot(
        config_revision="cfg-2026-09-01",
        root_model_config_id="root-default",
        child_model_config_ids=("logs", "db"),
        model_digest="sha256:model",
        skill_digest="sha256:skills",
        tool_digest="sha256:tools",
    )
    assert snapshot.child_model_config_ids == ("logs", "db")
    with pytest.raises(ValueError):
        ConfigSnapshot(
            config_revision="",
            root_model_config_id="root-default",
            child_model_config_ids=(),
            model_digest="sha256:model",
            skill_digest="sha256:skills",
            tool_digest="sha256:tools",
        )


def test_all_timestamps_are_timezone_aware_and_generation_cannot_be_negative() -> None:
    now = datetime(2026, 9, 1, tzinfo=UTC)
    assert (
        EventRecord(
            event_id="evt-1",
            ticket_id=3726,
            occurred_at=now,
            source="zendesk",
            customer_origin="customer",
            payload={},
        ).occurred_at.tzinfo
        is not None
    )
    state_table = cast(Table, TicketState.__table__)
    checks = {constraint.name for constraint in state_table.constraints}
    assert "ck_ticket_states_generation_nonnegative" in checks


def test_sqlite_ddl_is_available_for_local_contract_review() -> None:
    dialect = sqlite.dialect()
    ddl = str(CreateTable(cast(Table, CaseJobRow.__table__)).compile(dialect=dialect))
    assert "config_revision" in ddl
    assert "lease_owner" in ddl
    assert "generation" in ddl
