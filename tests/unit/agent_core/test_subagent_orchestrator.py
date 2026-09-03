from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from casework_agent_core.subagents import (
    ChildSession,
    SubagentOrchestrator,
    SubagentReport,
    SubagentSlot,
    SubagentTask,
)
from casework_tool_sdk.registry import ToolRegistry


def _new_sessions() -> list[ChildSession]:
    return []


def _new_registries() -> list[ToolRegistry]:
    return []


@dataclass
class FakeChildRunner:
    sessions: list[ChildSession] = field(default_factory=_new_sessions)
    registries: list[ToolRegistry] = field(default_factory=_new_registries)

    async def run(
        self,
        *,
        session: ChildSession,
        task: SubagentTask,
        tools: ToolRegistry,
        cancelled: asyncio.Event,
    ) -> SubagentReport:
        self.sessions.append(session)
        self.registries.append(tools)
        return SubagentReport(
            task_id=task.task_id,
            summary=f"Investigated with {session.model}",
            evidence_ids=(f"ev-{task.task_id}",),
        )


def _slot(slot_id: str, model: str) -> SubagentSlot:
    return SubagentSlot(
        slot_id=slot_id,
        provider="openai",
        model=model,
        credential_ref=f"secret/{slot_id}",
        context_window=32_000,
        max_tool_rounds=12,
        max_parallel=2,
    )


@pytest.mark.asyncio
async def test_children_use_explicit_models_isolated_sessions_and_shared_tools(
    tmp_path: Path,
) -> None:
    tools = ToolRegistry()
    runner = FakeChildRunner()
    orchestrator = SubagentOrchestrator(
        root_session_id="root-1",
        sandbox_workdir=tmp_path,
        slots=(_slot("logs", "gpt-5.6-luna"), _slot("db", "gpt-5.6-terra")),
        tools=tools,
        runner=runner,
    )

    reports = await orchestrator.dispatch_many(
        (
            ("logs", SubagentTask(task_id="logs-1", objective="inspect logs")),
            ("db", SubagentTask(task_id="db-1", objective="inspect rows")),
        )
    )

    assert [session.model for session in runner.sessions] == [
        "gpt-5.6-luna",
        "gpt-5.6-terra",
    ]
    assert all(session.parent_session_id == "root-1" for session in runner.sessions)
    assert len({session.session_id for session in runner.sessions}) == 2
    assert len({session.workdir for session in runner.sessions}) == 2
    workdirs_exist = await asyncio.gather(
        *(
            asyncio.to_thread(Path(session.workdir).is_dir)
            for session in runner.sessions
        )
    )
    assert all(workdirs_exist)
    assert runner.registries == [tools, tools]
    assert [report.task_id for report in reports] == ["logs-1", "db-1"]


@pytest.mark.asyncio
async def test_cancel_prevents_new_child_session(tmp_path: Path) -> None:
    orchestrator = SubagentOrchestrator(
        root_session_id="root-1",
        sandbox_workdir=tmp_path,
        slots=(_slot("logs", "gpt-5.6-luna"),),
        tools=ToolRegistry(),
        runner=FakeChildRunner(),
    )
    orchestrator.cancel()

    with pytest.raises(asyncio.CancelledError):
        await orchestrator.dispatch(
            "logs", SubagentTask(task_id="logs-1", objective="inspect logs")
        )


def test_enabled_child_slot_requires_its_own_credentials() -> None:
    with pytest.raises(ValueError, match="credential_ref"):
        SubagentSlot(
            slot_id="logs",
            provider="openai",
            model="gpt-5.6-luna",
            credential_ref="",
            context_window=32_000,
            max_tool_rounds=12,
            max_parallel=1,
        )
