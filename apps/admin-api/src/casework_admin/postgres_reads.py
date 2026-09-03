"""PostgreSQL-backed, evidence-honest Admin console read models."""

from __future__ import annotations

from collections import Counter
from collections.abc import AsyncGenerator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import UTC, datetime
from typing import Literal, cast

from pydantic import BaseModel
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
    RunActiveQuestion,
    RunContextCheckpoint,
    RunListResponse,
    RunRecord,
    RunSandboxLifecycle,
    RunSandboxLifecycleEvent,
    RunStatus,
    RunSubagentTask,
    RunTodo,
    RunToolCall,
    SkillRecord,
    ToolProbe,
    ToolRecord,
)
from casework_persistence.models import CaseJobRow, RunRecordRow
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


def _read_model[M: BaseModel](model_type: type[M], value: object) -> M | None:
    if not isinstance(value, dict):
        return None
    try:
        return model_type.model_validate(value)
    except ValueError:
        return None


def _read_models[M: BaseModel](model_type: type[M], value: object) -> tuple[M, ...]:
    if not isinstance(value, list):
        return ()
    raw_values = cast(list[object], value)
    parsed = tuple(
        item
        for raw in raw_values
        if (item := _read_model(model_type, raw)) is not None
    )
    return parsed


def _record(row: CaseJobRow, run: RunRecordRow | None = None) -> RunRecord:
    status = _run_status(row.status)
    has_telemetry = run is not None and run.telemetry_version is not None
    active_question = (
        _read_model(RunActiveQuestion, run.active_question)
        if has_telemetry and run is not None
        else None
    )
    todos = (
        _read_models(RunTodo, run.todo_statuses)
        if has_telemetry and run is not None
        else ()
    )
    tool_call_records = (
        _read_models(RunToolCall, run.tool_call_records)
        if has_telemetry and run is not None
        else ()
    )
    subagent_tasks = (
        _read_models(RunSubagentTask, run.subagent_tasks)
        if has_telemetry and run is not None
        else ()
    )
    context_checkpoint = (
        _read_model(RunContextCheckpoint, run.context_checkpoint)
        if has_telemetry and run is not None
        else None
    )
    lifecycle_events = (
        _read_models(RunSandboxLifecycleEvent, run.sandbox_lifecycle)
        if has_telemetry and run is not None
        else ()
    )
    sandbox_lifecycle = (
        RunSandboxLifecycle(
            sandbox_id=lifecycle_events[0].sandbox_id if lifecycle_events else None,
            attempt=lifecycle_events[0].attempt if lifecycle_events else None,
            events=lifecycle_events,
        )
        if has_telemetry
        else None
    )
    outcome_payload = run.outcome if run is not None else {}
    outcome_kind = outcome_payload.get("kind")
    outcome_reason = outcome_payload.get("reason")
    retry_classification = outcome_payload.get("retry_classification")
    snapshot_cursor = outcome_payload.get("snapshot_cursor")
    return RunRecord(
        ticket_id=row.ticket_id,
        subject=f"Ticket #{row.ticket_id}",
        status=status,
        status_label=_status_label(status),
        started_at=_timestamp(row.created_at),
        duration=_duration(row),
        # The queue row does not persist runtime activity.  Do not turn
        # unavailable Todo/tool/evidence/checkpoint data into fake zeros.
        steps=run.todo_total if has_telemetry and run is not None else None,
        session_id=row.session_id,
        event_id=row.event_id,
        expected_event_id=row.expected_event_id,
        attempts=row.attempts,
        question=(active_question.original_request if active_question else None),
        todo_completed=(
            run.todo_completed if has_telemetry and run is not None else None
        ),
        todo_total=run.todo_total if has_telemetry and run is not None else None,
        tool_calls=run.tool_calls if has_telemetry and run is not None else None,
        subagents=run.subagents if has_telemetry and run is not None else None,
        evidence=run.evidence_count if has_telemetry and run is not None else None,
        checkpoint=(context_checkpoint.checkpoint_ref if context_checkpoint else None),
        config_revision=row.config_revision,
        sandbox="running" if row.status in _ACTIVE else "destroyed",
        outcome=outcome_kind if isinstance(outcome_kind, str) else row.status,
        failure_reason=(
            outcome_reason
            if isinstance(outcome_reason, str)
            else row.dead_letter_reason
        ),
        retry_classification=(
            retry_classification
            if isinstance(retry_classification, str)
            else None
        ),
        snapshot_cursor=(
            snapshot_cursor if isinstance(snapshot_cursor, str) else None
        ),
        active_question=active_question,
        todos=cast(tuple[RunTodo, ...], todos),
        tool_call_records=cast(tuple[RunToolCall, ...], tool_call_records),
        subagent_tasks=cast(tuple[RunSubagentTask, ...], subagent_tasks),
        evidence_ids=(
            tuple(run.evidence_ids)
            if has_telemetry and run is not None and isinstance(run.evidence_ids, list)
            else ()
        ),
        context_checkpoint=context_checkpoint,
        sandbox_lifecycle=sandbox_lifecycle,
    )


def _configured_ids(value: object) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    items = cast(list[object] | tuple[object, ...], value)
    return tuple(item for item in items if isinstance(item, str) and item.strip())


class PostgresAdminReadRepository:
    """Project only fields currently persisted; unknown activity stays null."""

    def __init__(
        self,
        *,
        transaction_scope: TransactionScope,
        tool_probe: ToolProbe | None = None,
    ) -> None:
        self._transaction_scope = transaction_scope
        self._tool_probe = tool_probe

    async def _jobs(self) -> tuple[CaseJobRow, ...]:
        async with self._transaction_scope() as session:
            result = await session.execute(
                select(CaseJobRow).order_by(desc(CaseJobRow.created_at)).limit(500)
            )
            return tuple(result.scalars().all())

    async def _jobs_with_runs(
        self,
    ) -> tuple[tuple[CaseJobRow, RunRecordRow | None], ...]:
        async with self._transaction_scope() as session:
            result = await session.execute(
                select(CaseJobRow, RunRecordRow)
                .outerjoin(RunRecordRow, RunRecordRow.job_id == CaseJobRow.job_id)
                .order_by(desc(CaseJobRow.created_at))
                .limit(500)
            )
            return cast(
                tuple[tuple[CaseJobRow, RunRecordRow | None], ...], result.all()
            )

    async def overview(
        self,
        *,
        desired_state: Literal["running", "paused"],
        config_revision: str,
    ) -> OverviewResponse:
        pairs = await self._jobs_with_runs()
        jobs = tuple(row for row, _run in pairs)
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
        records = tuple(_record(row, run) for row, run in pairs)
        run_by_job = {run.job_id: run for _row, run in pairs if run is not None}
        failures = tuple(
            FailureItem(
                id=row.job_id,
                title=f"Ticket #{row.ticket_id}",
                detail=(
                    str(run_by_job[row.job_id].outcome.get("reason"))
                    if row.job_id in run_by_job
                    and run_by_job[row.job_id].outcome.get("reason")
                    else row.dead_letter_reason or "Harness job failed"
                ),
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
                    "event_id",
                    "expected_event_id",
                    "attempts",
                    "question",
                    "todo_completed",
                    "todo_total",
                    "tool_calls",
                    "subagents",
                    "evidence",
                    "checkpoint",
                    "config_revision",
                    "sandbox",
                    "outcome",
                    "failure_reason",
                    "retry_classification",
                    "snapshot_cursor",
                    "active_question",
                    "todos",
                    "tool_call_records",
                    "subagent_tasks",
                    "evidence_ids",
                    "context_checkpoint",
                    "sandbox_lifecycle",
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
            for record in (
                _record(row, run) for row, run in await self._jobs_with_runs()
            )
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
                select(CaseJobRow, RunRecordRow)
                .outerjoin(RunRecordRow, RunRecordRow.job_id == CaseJobRow.job_id)
                .where(CaseJobRow.session_id == session_id)
            )
            pair = result.one_or_none()
        return None if pair is None else _record(pair[0], pair[1])

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
        if not configured:
            return ConnectionProbeResponse(
                tool_id=tool_id,
                status="untested",
                latency_ms=None,
                error_code="unknown_tool",
                detail="Tool is not registered",
            )
        if self._tool_probe is not None:
            return await self._tool_probe.probe(tool_id)
        return ConnectionProbeResponse(
            tool_id=tool_id,
            status="untested",
            latency_ms=None,
            error_code="not_configured",
            detail="No probe endpoint is configured for this tool",
        )


__all__ = ["PostgresAdminReadRepository", "postgres_read_transaction_scope"]
