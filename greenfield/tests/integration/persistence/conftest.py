"""Real PostgreSQL fixtures for persistence integration tests.

These tests intentionally accept only an explicit ``TEST_DATABASE_URL``.  A
missing URL skips the suite; no SQLite fallback is used for PostgreSQL claims.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import AsyncIterator, Callable, Iterator
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from casework_persistence.db import create_engine, create_session_factory

GREENFIELD_ROOT = Path(__file__).resolve().parents[3]
TABLES = (
    "outbox_messages, case_jobs, ticket_states, events, "
    "config_head, config_revisions"
)


def _async_database_url() -> str:
    raw = os.environ.get("TEST_DATABASE_URL")
    if not raw:
        pytest.skip("TEST_DATABASE_URL is required for real PostgreSQL tests")
    if raw.startswith("postgresql+asyncpg://"):
        return raw
    if raw.startswith("postgresql://"):
        return "postgresql+asyncpg://" + raw.removeprefix("postgresql://")
    raise pytest.UsageError("TEST_DATABASE_URL must be a PostgreSQL URL")


def _run_alembic(database_url: str, *arguments: str) -> None:
    environment = os.environ.copy()
    environment["DATABASE_URL"] = database_url
    subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", *arguments],
        cwd=GREENFIELD_ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.fixture(scope="module")
def postgres_database_url() -> Iterator[str]:
    database_url = _async_database_url()
    _run_alembic(database_url, "upgrade", "head")
    try:
        yield database_url
    finally:
        _run_alembic(database_url, "downgrade", "base")


@pytest.fixture(scope="module")
def migration_runner(
    postgres_database_url: str,
) -> Callable[..., None]:
    """Run Alembic against the explicitly supplied test database."""

    def run(*arguments: str) -> None:
        _run_alembic(postgres_database_url, *arguments)

    return run


@pytest.fixture
async def postgres_engine(postgres_database_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_engine(postgres_database_url)
    try:
        async with engine.begin() as connection:
            await connection.execute(text(f"TRUNCATE {TABLES} CASCADE"))
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
async def postgres_sessions(
    postgres_engine: AsyncEngine,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    yield create_session_factory(postgres_engine)
