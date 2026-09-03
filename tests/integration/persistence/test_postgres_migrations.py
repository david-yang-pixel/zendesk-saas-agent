from __future__ import annotations

from collections.abc import Callable

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from casework_persistence.db import SCHEMA_HEAD, create_engine


@pytest.mark.asyncio
async def test_real_postgres_is_at_head_and_can_round_trip_to_base(
    postgres_database_url: str,
    migration_runner: Callable[..., None],
) -> None:
    engine: AsyncEngine = create_engine(postgres_database_url)
    try:
        async with engine.connect() as connection:
            version = await connection.scalar(
                text("SELECT version_num FROM alembic_version")
            )
        assert version == SCHEMA_HEAD

        migration_runner("downgrade", "base")
        async with engine.connect() as connection:
            remaining = await connection.scalar(
                text(
                    "SELECT count(*) FROM information_schema.tables "
                    "WHERE table_schema = 'public' AND table_name IN "
                    "('events', 'ticket_states', 'case_jobs', 'outbox_messages')"
                )
            )
        assert remaining == 0

        migration_runner("upgrade", "head")
        async with engine.connect() as connection:
            restored = await connection.scalar(
                text("SELECT version_num FROM alembic_version")
            )
        assert restored == SCHEMA_HEAD
    finally:
        await engine.dispose()
