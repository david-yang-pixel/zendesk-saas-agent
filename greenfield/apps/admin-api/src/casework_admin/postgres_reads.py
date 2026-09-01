"""PostgreSQL-backed, evidence-honest Admin console read models."""

from __future__ import annotations

from collections import Counter
from collections.abc import AsyncGenerator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import UTC, datetime
from typing import Literal, cast

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from casework_admin.read_models import (
    ConnectionProbeResponse,
    DashboardMetric,
    FailureItem,
    OverviewResponse,
    QueueHealth,
    RecentRun,
    RegistryResponse,
    RunListResponse,
    RunRecord,
    RunStatus,
    SkillRecord,
    ToolRecord,
)
from casework_persistence.models import CaseJobRow
from casework_persistence.repositories import (
    ConfigRevisionRepository,
    SessionPort,
)

TransactionScope = Callable[[], AbstractAsyncContextManager[SessionPort]]
_ACTIVE = frozenset({"queued", "claimed", "sandbox_creating", "running", "finalizing"})


def postgres_read_transaction_scope(
    sessions: async_sessionmaker[AsyncSession],
) -> TransactionScope:
    @asynccontextmanager
    async def scope() -> AsyncGenerator[SessionPort, None]:
        async with sessions() as session, session.begin():
            yield cast(SessionPort, session)

    return scope


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _timestamp(value: datetime | None) -> str:
    if value is None:
        return ""
    return _utc(value).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _run_status(status: str) -> RunStatus:
    if status in _ACTIVE:
        return "running"
    return cast(RunStatus, status)


def _status_label(status: RunStatus) -> str:
    return {
        "running": "运行中",
        "awaiting_customer": "等待客户",
        "completed": "已完成",
        "human_takeover": "人工接管",
        "failed": "失败",
        "cancelled": "已取消",
    }[status]


def _duration(row: CaseJobRow) -> str:
    start = _utc(row.created_at)
    end = _utc(row.finished_at) if row.finished_at is not None else datetime.now(UTC)
    seconds = max(0, int((end - start).total_seconds()))
    minutes, remainder = divmod(seconds, 60)
    return f"{minutes}m {remainder}s" if minutes else f"{remainder}s"


def _record(row: CaseJobRow) -> RunRecord:
    status = _run_status(row.status)
    return RunRecord(
        ticket_id=row.ticket_id,
        subject=f"Ticket #{row.ticket_id}",
        status=status,
        status_label=_status_label(status),
        started_at=_timestamp(row.created_at),
        duration=_duration(row),
        # The queue row does not persist runtime activity.  Do not turn
        # unavailable Todo/tool/evidence/checkpoint data into fake zeros.
        steps=None,
        session_id=row.session_id,
        question=None,
        todo_completed=None,
        todo_total=None,
        tool_calls=None,
        subagents=None,
        evidence=None,
        checkpoint=None,
        sandbox="running" if row.status in _ACTIVE else "destroyed",
        outcome=row.status,
    )


def _configured_ids(value: object) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    items = cast(list[object] | tuple[object, ...], value)
    return tuple(item for item in items if isinstance(item, str) and item.strip())


class PostgresAdminReadRepository:
    """Project only fields currently persisted; unknown activity stays null."""

    def __init__(self, *, transaction_scope: TransactionScope) -> None:
        self._transaction_scope = transaction_scope

    async def _jobs(self) -> tuple[CaseJobRow, ...]:
        async with self._transaction_scope() as session:
            result = await session.execute(
                select(CaseJobRow).order_by(desc(CaseJobRow.created_at)).limit(500)
            )
            return tuple(result.scalars().all())

    async def overview(
        self,
        *,
        desired_state: Literal["running", "paused"],
        config_revision: str,
    ) -> OverviewResponse:
        jobs = await self._jobs()
        statuses = Counter(row.status for row in jobs)
        active = sum(statuses[status] for status in _ACTIVE)
        waiting = statuses["awaiting_customer"]
        completed = statuses["completed"]
        closed_other = len(jobs) - active - waiting - completed
        queue = (
            QueueHealth(
                label="Active",
                count=active,
                description="Queued, leased, or running jobs",
                tone="info",
            ),
            QueueHealth(
                label="Awaiting customer",
                count=waiting,
                description="Runs waiting for customer clarification",
                tone="warning",
            ),
            QueueHealth(
                label="Completed",
                count=completed,
                description="Completed durable jobs",
                tone="success",
            ),
            QueueHealth(
                label="Other terminal",
                count=closed_other,
                description="Failed, cancelled, or human-takeover jobs",
                tone="danger" if statuses["failed"] else "neutral",
            ),
        )
        records = tuple(_record(row) for row in jobs)
        failures = tuple(
            FailureItem(
                id=row.job_id,
                title=f"Ticket #{row.ticket_id}",
                detail=row.dead_letter_reason or "Harness job failed",
                time=_timestamp(row.finished_at or row.updated_at),
                tone="danger",
            )
            for row in jobs
            if row.status == "failed"
        )[:10]
        return OverviewResponse(
            desired_state=desired_state,
            config_revision=config_revision,
            heartbeat=next(
                (
                    _timestamp(row.heartbeat_at)
                    for row in jobs
                    if row.heartbeat_at is not None
                ),
                None,
            ),
            metrics=(
                DashboardMetric(
                    label="Durable jobs",
                    value=str(len(jobs)),
                    detail="Latest persisted jobs",
                    icon="queue",
                    accent="blue",
                ),
                DashboardMetric(
                    label="Active",
                    value=str(active),
                    detail="Currently claimable or leased",
                    icon="activity",
                    accent="green",
                ),
                DashboardMetric(
                    label="Awaiting customer",
                    value=str(waiting),
                    detail="Clarification requested",
                    icon="message",
                    accent="amber",
                ),
                DashboardMetric(
                    label="Failed",
                    value=str(statuses["failed"]),
                    detail="Requires operator review",
                    icon="alert",
                    accent="violet",
                ),
            ),
            queue_health=queue,
            failures=failures,
            recent_runs=tuple(
                RecentRun(**record.model_dump(exclude={
                    "session_id",
                    "question",
                    "todo_completed",
                    "todo_total",
                    "tool_calls",
                    "subagents",
                    "evidence",
                    "checkpoint",
                    "sandbox",
                    "outcome",
                }))
                for record in records[:10]
            ),
        )

    async def runs(
        self,
        *,
        query: str | None = None,
        status: RunStatus | None = None,
    ) -> RunListResponse:
        normalized = query.strip().casefold() if query else ""
        records = tuple(
            record
            for record in (_record(row) for row in await self._jobs())
            if (status is None or record.status == status)
            and (
                not normalized
                or normalized in str(record.ticket_id)
                or normalized in record.session_id.casefold()
                or normalized in record.subject.casefold()
            )
        )
        return RunListResponse(items=records, total=len(records))

    async def run(self, *, session_id: str) -> RunRecord | None:
        async with self._transaction_scope() as session:
            result = await session.execute(
                select(CaseJobRow).where(CaseJobRow.session_id == session_id)
            )
            row = result.scalar_one_or_none()
        return None if row is None else _record(row)

    async def registry(self) -> RegistryResponse:
        async with self._transaction_scope() as session:
            revision = await ConfigRevisionRepository(session).current()
        skills = _configured_ids(revision.config.get("enabled_skills"))
        tools = _configured_ids(revision.config.get("enabled_tools"))
        updated_at = _timestamp(revision.created_at)
        return RegistryResponse(
            skills=tuple(
                SkillRecord(
                    id=skill_id,
                    name=skill_id,
                    description="Enabled by the current durable configuration",
                    version=revision.revision,
                    assigned_todos="runtime-defined",
                    status="enabled",
                    updated_at=updated_at,
                )
                for skill_id in skills
            ),
            tools=tuple(
                ToolRecord(
                    id=tool_id,
                    name=tool_id,
                    description="Enabled by the current durable configuration",
                    scope="root + subagents",
                    contract="registered runtime contract",
                    connection="untested",
                    latency="—",
                )
                for tool_id in tools
            ),
        )

    async def probe_tool(self, *, tool_id: str) -> ConnectionProbeResponse:
        registry = await self.registry()
        configured = any(tool.id == tool_id for tool in registry.tools)
        return ConnectionProbeResponse(
            tool_id=tool_id,
            status="untested" if configured else "degraded",
            latency_ms=None,
        )


__all__ = ["PostgresAdminReadRepository", "postgres_read_transaction_scope"]
