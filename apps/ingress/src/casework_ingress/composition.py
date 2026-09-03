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
from casework_ingress.zendesk import ZendeskApiTicketReader
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

    base_url = os.environ.get("ZENDESK_BASE_URL", "").strip()
    if not base_url:
        subdomain = os.environ.get("ZENDESK_SUBDOMAIN", "").strip()
        if subdomain:
            base_url = f"https://{subdomain}.zendesk.com"
    email = (
        os.environ.get("ZENDESK_EMAIL", "").strip()
        or os.environ.get("ZENDESK_ACCOUNT_EMAIL", "").strip()
    )
    api_token = os.environ.get("ZENDESK_API_TOKEN", "").strip()
    signing_secret = (
        os.environ.get("ZENDESK_MESSAGING_WEBHOOK_SECRET", "").strip()
        or os.environ.get("ZENDESK_MESSAGING_SIGNING_SECRET", "").strip()
    )
    if not base_url:
        raise RuntimeError("ZENDESK_BASE_URL or ZENDESK_SUBDOMAIN is required")
    if not email:
        raise RuntimeError("ZENDESK_EMAIL is required")
    if not api_token:
        raise RuntimeError("ZENDESK_API_TOKEN is required")
    if not signing_secret:
        raise RuntimeError("ZENDESK_MESSAGING_WEBHOOK_SECRET is required")

    timeout_value = os.environ.get("ZENDESK_TIMEOUT_SECONDS", "10").strip()
    try:
        timeout_seconds = float(timeout_value)
    except ValueError as error:
        raise RuntimeError("ZENDESK_TIMEOUT_SECONDS must be numeric") from error
    max_age_value = os.environ.get("ZENDESK_WEBHOOK_MAX_AGE_SECONDS", "300").strip()
    try:
        max_age_seconds = float(max_age_value)
    except ValueError as error:
        raise RuntimeError(
            "ZENDESK_WEBHOOK_MAX_AGE_SECONDS must be numeric"
        ) from error
    if max_age_seconds < 0:
        raise RuntimeError("ZENDESK_WEBHOOK_MAX_AGE_SECONDS must not be negative")
    reader = ZendeskApiTicketReader(
        base_url=base_url,
        email=email,
        api_token=api_token,
        timeout_seconds=timeout_seconds,
    )

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
            await reader.close()
            await engine.dispose()

    return create_ingress_app(
        admission=PostgresAdmission(transaction_scope=transactions),
        readiness=readiness,
        lifespan=lifespan,
        ticket_reader=reader,
        messaging_signing_secret=signing_secret,
        messaging_signature_max_age_seconds=max_age_seconds,
    )


__all__ = ["create_app"]
