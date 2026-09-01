"""PostgreSQL-backed durable queue adapter for the Harness worker."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Protocol, cast

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from casework_harness.worker import JobLease
from casework_persistence.models import CaseJobRow
from casework_persistence.repositories import JobRepository


class LeaseLostError(RuntimeError):
    """The worker no longer owns the durable job lease."""


class JobGateway(Protocol):
    async def claim_next(
        self, *, worker_id: str, lease_seconds: int, max_attempts: int
    ) -> CaseJobRow | None: ...

    async def transition(
        self, *, job_id: str, worker_id: str, status: str
    ) -> bool: ...

    async def heartbeat(
        self, *, job_id: str, worker_id: str, lease_seconds: int
    ) -> bool: ...

    async def finish(
        self,
        *,
        job_id: str,
        worker_id: str,
        status: str,
        dead_letter_reason: str | None = None,
    ) -> bool: ...


GatewayScope = Callable[[], AbstractAsyncContextManager[JobGateway]]


def postgres_job_gateway_scope(
    sessions: async_sessionmaker[AsyncSession],
) -> GatewayScope:
    @asynccontextmanager
    async def scope() -> AsyncGenerator[JobGateway, None]:
        async with sessions() as session, session.begin():
            yield cast(JobGateway, JobRepository(session))

    return scope


class PostgresJobQueue:
    """Map repository rows to leases and reject stale-worker mutations."""

    def __init__(
        self,
        *,
        gateway_scope: GatewayScope,
        worker_id: str,
        lease_seconds: int = 90,
        max_attempts: int = 5,
    ) -> None:
        if not worker_id.strip():
            raise ValueError("worker_id must not be blank")
        if lease_seconds <= 0 or max_attempts <= 0:
            raise ValueError("lease_seconds and max_attempts must be positive")
        self._gateway_scope = gateway_scope
        self._worker_id = worker_id
        self._lease_seconds = lease_seconds
        self._max_attempts = max_attempts

    async def claim(self) -> JobLease | None:
        async with self._gateway_scope() as jobs:
            row = await jobs.claim_next(
                worker_id=self._worker_id,
                lease_seconds=self._lease_seconds,
                max_attempts=self._max_attempts,
            )
        return None if row is None else self._lease(row)

    async def transition(self, lease: JobLease, status: str) -> None:
        async with self._gateway_scope() as jobs:
            updated = await jobs.transition(
                job_id=lease.job_id,
                worker_id=self._worker_id,
                status=status,
            )
        self._require_lease(updated, lease)

    async def heartbeat(self, lease: JobLease) -> None:
        async with self._gateway_scope() as jobs:
            updated = await jobs.heartbeat(
                job_id=lease.job_id,
                worker_id=self._worker_id,
                lease_seconds=self._lease_seconds,
            )
        self._require_lease(updated, lease)

    async def finish(self, lease: JobLease, status: str) -> None:
        async with self._gateway_scope() as jobs:
            updated = await jobs.finish(
                job_id=lease.job_id,
                worker_id=self._worker_id,
                status=status,
                dead_letter_reason=(
                    "harness_failed" if status == "failed" else None
                ),
            )
        self._require_lease(updated, lease)

    @staticmethod
    def _lease(row: CaseJobRow) -> JobLease:
        return JobLease(
            job_id=row.job_id,
            session_id=row.session_id,
            ticket_id=row.ticket_id,
            generation=row.generation,
            config_revision=row.config_revision,
            model_digest=row.model_digest,
            skill_digest=row.skill_digest,
            tool_digest=row.tool_digest,
        )

    @staticmethod
    def _require_lease(updated: bool, lease: JobLease) -> None:
        if not updated:
            raise LeaseLostError(f"lease lost for job {lease.job_id}")


__all__ = [
    "LeaseLostError",
    "PostgresJobQueue",
    "postgres_job_gateway_scope",
]
