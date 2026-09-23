"""Async SQLAlchemy engine/session helpers."""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

SCHEMA_HEAD = "0010_internal_draft_deliveries"


def create_engine(database_url: str, *, echo: bool = False) -> AsyncEngine:
    """Create a pooled PostgreSQL async engine from configuration."""

    return create_async_engine(
        database_url,
        echo=echo,
        pool_pre_ping=True,
        pool_recycle=1800,
    )


def create_session_factory(
    engine: AsyncEngine,
) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


class DatabaseReadiness:
    """Probe connectivity and require the exact Alembic schema head."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        expected_revision: str,
    ) -> None:
        self._sessions = sessions
        self._expected_revision = expected_revision

    async def check(self) -> bool:
        try:
            async with self._sessions() as session:
                await session.execute(text("SELECT 1"))
                result = await session.execute(
                    text("SELECT version_num FROM alembic_version")
                )
                return result.scalar_one_or_none() == self._expected_revision
        except (OSError, SQLAlchemyError):
            return False


async def session_scope(
    factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """Yield one session; callers decide transaction boundaries."""

    async with factory() as session:
        yield session


__all__ = [
    "SCHEMA_HEAD",
    "DatabaseReadiness",
    "create_engine",
    "create_session_factory",
    "session_scope",
]
