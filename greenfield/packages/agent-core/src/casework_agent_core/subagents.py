"""Depth-one Subagent orchestration inside one ephemeral ticket sandbox."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from casework_tool_sdk.registry import ToolRegistry


@dataclass(frozen=True, slots=True)
class SubagentSlot:
    slot_id: str
    provider: str
    model: str
    credential_ref: str
    context_window: int
    max_tool_rounds: int
    max_parallel: int

    def __post_init__(self) -> None:
        for name in ("slot_id", "provider", "model", "credential_ref"):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"{name} must not be empty")
        if self.context_window <= 0:
            raise ValueError("context_window must be positive")
        if self.max_tool_rounds <= 0:
            raise ValueError("max_tool_rounds must be positive")
        if not 1 <= self.max_parallel <= 8:
            raise ValueError("max_parallel must be between 1 and 8")


@dataclass(frozen=True, slots=True)
class SubagentTask:
    task_id: str
    objective: str
    required_evidence: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.task_id.strip() or not self.objective.strip():
            raise ValueError("Subagent task requires task_id and objective")


@dataclass(frozen=True, slots=True)
class ChildSession:
    session_id: str
    root_session_id: str
    parent_session_id: str
    slot_id: str
    provider: str
    model: str
    credential_ref: str
    context_window: int
    max_tool_rounds: int
    workdir: str


@dataclass(frozen=True, slots=True)
class SubagentReport:
    task_id: str
    summary: str
    evidence_ids: tuple[str, ...] = ()
    artifact_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.task_id.strip() or not self.summary.strip():
            raise ValueError("Subagent report requires task_id and summary")
        if len(self.summary) > 4_000:
            raise ValueError("Subagent report summary exceeds 4000 characters")
        if len(self.evidence_ids) > 64 or len(self.artifact_refs) > 64:
            raise ValueError("Subagent report references exceed bounded limit")


class ChildAgentRunner(Protocol):
    async def run(
        self,
        *,
        session: ChildSession,
        task: SubagentTask,
        tools: ToolRegistry,
        cancelled: asyncio.Event,
    ) -> SubagentReport: ...


class SubagentOrchestrator:
    """Dispatch explicit child slots without exposing recursive orchestration."""

    def __init__(
        self,
        *,
        root_session_id: str,
        sandbox_workdir: Path,
        slots: tuple[SubagentSlot, ...],
        tools: ToolRegistry,
        runner: ChildAgentRunner,
    ) -> None:
        if not root_session_id.strip():
            raise ValueError("root_session_id must not be empty")
        slot_map = {slot.slot_id: slot for slot in slots}
        if len(slot_map) != len(slots):
            raise ValueError("Subagent slot IDs must be unique")
        self._root_session_id = root_session_id
        self._sandbox_workdir = sandbox_workdir
        self._slots = slot_map
        self._tools = tools.for_session(kind="subagent")
        self._runner = runner
        self._cancelled = asyncio.Event()
        self._limits = {
            slot.slot_id: asyncio.Semaphore(slot.max_parallel) for slot in slots
        }

    def cancel(self) -> None:
        self._cancelled.set()

    async def dispatch(self, slot_id: str, task: SubagentTask) -> SubagentReport:
        if self._cancelled.is_set():
            raise asyncio.CancelledError
        try:
            slot = self._slots[slot_id]
        except KeyError as error:
            raise LookupError(f"Subagent slot is not configured: {slot_id}") from error
        async with self._limits[slot_id]:
            if self._cancelled.is_set():
                raise asyncio.CancelledError
            session = self._create_session(slot)
            report = await self._runner.run(
                session=session,
                task=task,
                tools=self._tools,
                cancelled=self._cancelled,
            )
            if report.task_id != task.task_id:
                raise ValueError("Subagent report does not match dispatched task")
            return report

    async def dispatch_many(
        self, work: tuple[tuple[str, SubagentTask], ...]
    ) -> tuple[SubagentReport, ...]:
        reports = await asyncio.gather(
            *(self.dispatch(slot_id, task) for slot_id, task in work)
        )
        return tuple(reports)

    def _create_session(self, slot: SubagentSlot) -> ChildSession:
        session_id = f"sub-{uuid4()}"
        workdir = self._sandbox_workdir / "subagents" / session_id
        workdir.mkdir(parents=True, exist_ok=False)
        return ChildSession(
            session_id=session_id,
            root_session_id=self._root_session_id,
            parent_session_id=self._root_session_id,
            slot_id=slot.slot_id,
            provider=slot.provider,
            model=slot.model,
            credential_ref=slot.credential_ref,
            context_window=slot.context_window,
            max_tool_rounds=slot.max_tool_rounds,
            workdir=str(workdir),
        )
