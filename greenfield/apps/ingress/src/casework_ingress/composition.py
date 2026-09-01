"""Production composition root for the webhook ingress process."""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy.exc import SQLAlchemyError

from casework_ingress.app import create_app as create_ingress_app
from casework_ingress.postgres_admission import (
    PostgresAdmission,
    TransactionScope,
    postgres_transaction_scope,
)
from casework_persistence import (
    SCHEMA_HEAD,
    ConfigRevisionMissing,
    ConfigRevisionRepository,
    DatabaseReadiness,
    create_engine,
    create_session_factory,
)


class _IngressReadiness:
    def __init__(
        self,
        *,
        database: DatabaseReadiness,
        transaction_scope: TransactionScope,
    ) -> None:
        self._database = database
        self._transaction_scope = transaction_scope

    async def check(self) -> bool:
        if not await self._database.check():
            return False
        try:
            async with self._transaction_scope() as session:
                await ConfigRevisionRepository(session).current()
        except (ConfigRevisionMissing, OSError, SQLAlchemyError):
            return False
        return True


def create_app() -> FastAPI:
    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError("DATABASE_URL is required for production ingress")

    engine = create_engine(database_url)
    sessions = create_session_factory(engine)
    transactions = postgres_transaction_scope(sessions)
    database = DatabaseReadiness(sessions, expected_revision=SCHEMA_HEAD)
    readiness = _IngressReadiness(
        database=database,
        transaction_scope=transactions,
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
        try:
            yield
        finally:
            await engine.dispose()

    return create_ingress_app(
        admission=PostgresAdmission(transaction_scope=transactions),
        readiness=readiness,
        lifespan=lifespan,
    )


__all__ = ["create_app"]
