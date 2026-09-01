"""Read-only projections consumed by the five-page operations console.

The admin process intentionally exposes summaries rather than runtime internals.
The repository port keeps the HTTP layer independent from PostgreSQL and makes
the same API straightforward to exercise with an in-memory adapter.
"""

from __future__ import annotations

from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field


class _ReadModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DashboardMetric(_ReadModel):
    label: str
    value: str
    detail: str
    trend: str | None = None
    trend_tone: Literal["success", "warning", "danger", "neutral", "info"] | None = None
    icon: str
    accent: Literal["blue", "green", "amber", "violet"]


class QueueHealth(_ReadModel):
    label: str
    count: int = Field(ge=0)
    description: str
    tone: Literal["success", "warning", "danger", "neutral", "info"]


class FailureItem(_ReadModel):
    id: str
    title: str
    detail: str
    time: str
    tone: Literal["success", "warning", "danger", "neutral", "info"]


RunStatus = Literal[
    "running", "awaiting_customer", "completed", "human_takeover", "failed", "cancelled"
]
ProbeDetail = Literal[
    "Tool connection probe timed out",
    "Tool endpoint is unavailable",
    "No probe endpoint is configured for this tool",
    "Tool is not registered",
]


class RecentRun(_ReadModel):
    ticket_id: int = Field(gt=0)
    subject: str
    status: RunStatus
    status_label: str
    started_at: str
    duration: str
    steps: int | None = Field(default=None, ge=0)


class RunRecord(RecentRun):
    session_id: str
    # The queue snapshot pins each run to an immutable config revision.  Local
    # fixtures may omit it, so absence remains explicitly unknown.
    config_revision: str | None = None
    question: str | None = None
    todo_completed: int | None = Field(default=None, ge=0)
    todo_total: int | None = Field(default=None, ge=0)
    tool_calls: int | None = Field(default=None, ge=0)
    subagents: int | None = Field(default=None, ge=0)
    evidence: int | None = Field(default=None, ge=0)
    checkpoint: str | None = None
    sandbox: Literal["destroyed", "running"]
    outcome: str


class OverviewResponse(_ReadModel):
    desired_state: Literal["running", "paused"]
    config_revision: str
    heartbeat: str | None = None
    metrics: tuple[DashboardMetric, ...] = ()
    queue_health: tuple[QueueHealth, ...] = ()
    failures: tuple[FailureItem, ...] = ()
    recent_runs: tuple[RecentRun, ...] = ()


class RunListResponse(_ReadModel):
    items: tuple[RunRecord, ...] = ()
    total: int = Field(ge=0)


class SkillRecord(_ReadModel):
    id: str
    name: str
    description: str
    version: str
    assigned_todos: str
    status: Literal["enabled", "core", "disabled"]
    updated_at: str


class ToolRecord(_ReadModel):
    id: str
    name: str
    description: str
    scope: Literal["root + subagents", "root only"]
    contract: str
    connection: Literal["connected", "degraded", "untested"]
    latency: str


class RegistryResponse(_ReadModel):
    skills: tuple[SkillRecord, ...] = ()
    tools: tuple[ToolRecord, ...] = ()


class ConnectionProbeResponse(_ReadModel):
    tool_id: str
    status: Literal["connected", "degraded", "untested"]
    latency_ms: int | None = Field(default=None, ge=0)
    error_code: Literal[
        "timeout", "unavailable", "not_configured", "unknown_tool"
    ] | None = None
    detail: ProbeDetail | None = None


class ToolProbe(Protocol):
    async def probe(self, tool_id: str) -> ConnectionProbeResponse: ...


class AdminReadRepository(Protocol):
    async def overview(
        self,
        *,
        desired_state: Literal["running", "paused"],
        config_revision: str,
    ) -> OverviewResponse: ...

    async def runs(
        self, *, query: str | None = None, status: RunStatus | None = None
    ) -> RunListResponse: ...

    async def run(self, *, session_id: str) -> RunRecord | None: ...

    async def registry(self) -> RegistryResponse: ...

    async def probe_tool(self, *, tool_id: str) -> ConnectionProbeResponse: ...


class InMemoryAdminReadRepository:
    """Small local adapter; production can replace it with a PostgreSQL port."""

    def __init__(
        self,
        *,
        metrics: tuple[DashboardMetric, ...] = (),
        queue_health: tuple[QueueHealth, ...] = (),
        failures: tuple[FailureItem, ...] = (),
        recent_runs: tuple[RecentRun, ...] = (),
        runs: tuple[RunRecord, ...] = (),
        registry: RegistryResponse | None = None,
        tool_probe: ToolProbe | None = None,
    ) -> None:
        self._metrics = metrics
        self._queue_health = queue_health
        self._failures = failures
        self._recent_runs = recent_runs
        self._runs = runs
        self._registry = registry or RegistryResponse()
        self._tool_probe = tool_probe

    async def overview(
        self,
        *,
        desired_state: Literal["running", "paused"],
        config_revision: str,
    ) -> OverviewResponse:
        return OverviewResponse(
            desired_state=desired_state,
            config_revision=config_revision,
            metrics=self._metrics,
            queue_health=self._queue_health,
            failures=self._failures,
            recent_runs=self._recent_runs,
        )

    async def runs(
        self,
        *,
        query: str | None = None,
        status: RunStatus | None = None,
    ) -> RunListResponse:
        normalized = query.strip().casefold() if query else ""
        items = tuple(
            run
            for run in self._runs
            if (status is None or run.status == status)
            and (
                not normalized
                or normalized in str(run.ticket_id).casefold()
                or normalized in run.subject.casefold()
                or normalized in run.session_id.casefold()
            )
        )
        return RunListResponse(items=items, total=len(items))

    async def run(self, *, session_id: str) -> RunRecord | None:
        return next(
            (item for item in self._runs if item.session_id == session_id), None
        )

    async def registry(self) -> RegistryResponse:
        return self._registry

    async def probe_tool(self, *, tool_id: str) -> ConnectionProbeResponse:
        if self._tool_probe is not None:
            return await self._tool_probe.probe(tool_id)
        tool = next((item for item in self._registry.tools if item.id == tool_id), None)
        return ConnectionProbeResponse(
            tool_id=tool_id,
            status=tool.connection if tool is not None else "untested",
            latency_ms=None,
            error_code="not_configured" if tool is not None else "unknown_tool",
            detail=(
                "No probe endpoint is configured for this tool"
                if tool is not None
                else "Tool is not registered"
            ),
        )
