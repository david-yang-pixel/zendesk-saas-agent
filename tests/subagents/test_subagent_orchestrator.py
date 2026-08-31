from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from zd_agent.runtime.config_snapshot import (
    CaseworkRuntimeSnapshot,
    LLMRuntimeSnapshot,
    SubagentSlotRuntimeSnapshot,
)
from zd_agent.subagents.models import SubagentTaskStatus
from zd_agent.subagents.orchestrator import SubagentOrchestrator
from zd_agent.subagents.store import SubagentStore


def _llm(model: str) -> LLMRuntimeSnapshot:
    return LLMRuntimeSnapshot(
        provider="openai",
        model=model,
        reasoning_effort="low",
        enable_vision_input=False,
        temperature=0.0,
        base_url="https://llm.example.test/v1",
        timeout=60,
        max_tokens=4096,
        context_window_tokens=128_000,
        api_key=f"secret-{model}",
    )


def _snapshot() -> CaseworkRuntimeSnapshot:
    return CaseworkRuntimeSnapshot(
        main_llm=_llm("main"),
        subagents_enabled=True,
        subagent_slots=(
            SubagentSlotRuntimeSnapshot(id="reader", label="Reader", llm=_llm("reader")),
            SubagentSlotRuntimeSnapshot(id="logs", label="Logs", llm=_llm("logs")),
        ),
        subagent_max_parallel=2,
        subagent_max_tasks_per_run=4,
        subagent_task_timeout_seconds=30,
        tool_names=("read",),
        config_revision="revision-1",
        config_digest="digest-1",
    )


@pytest.mark.asyncio
async def test_orchestrator_runs_children_in_independent_sessions_and_slots(tmp_path) -> None:
    store = SubagentStore(tmp_path / "subagents.sqlite3")
    started = asyncio.Event()
    release = asyncio.Event()
    calls = []

    async def runner(task, slot, session):
        calls.append((task.id, slot.id, slot.llm.model, session.session_id))
        if len(calls) == 2:
            started.set()
        await release.wait()
        return {"summary": f"result from {slot.id}", "evidence": []}

    orchestrator = SubagentOrchestrator(
        store=store,
        snapshot=_snapshot(),
        root_session_id="root-1",
        ticket_id=4001,
        runner=runner,
    )

    first = orchestrator.spawn(
        slot_id="reader", objective="Read ticket", idempotency_key="ticket"
    )
    second = orchestrator.spawn(
        slot_id="logs", objective="Read logs", idempotency_key="logs"
    )
    await asyncio.wait_for(started.wait(), timeout=1)
    release.set()
    results = await orchestrator.wait((first.id, second.id), timeout_seconds=1)

    assert {task.status for task in results} == {SubagentTaskStatus.COMPLETED}
    assert {call[2] for call in calls} == {"reader", "logs"}
    assert len({call[3] for call in calls}) == 2
    assert all(call[3] != "root-1" for call in calls)


@pytest.mark.asyncio
async def test_orchestrator_enforces_configured_task_limit(tmp_path) -> None:
    snapshot = _snapshot()
    snapshot = CaseworkRuntimeSnapshot(
        **{**snapshot.__dict__, "subagent_max_tasks_per_run": 1}
    )

    async def runner(task, slot, session):
        _ = task, slot, session
        return {"summary": "done"}

    orchestrator = SubagentOrchestrator(
        store=SubagentStore(tmp_path / "subagents.sqlite3"),
        snapshot=snapshot,
        root_session_id="root-1",
        ticket_id=4001,
        runner=runner,
    )
    orchestrator.spawn(slot_id="reader", objective="one", idempotency_key="one")

    with pytest.raises(RuntimeError, match="task limit"):
        orchestrator.spawn(slot_id="logs", objective="two", idempotency_key="two")

    await orchestrator.cancel_all()


@pytest.mark.asyncio
async def test_spawn_normalizes_idempotency_key_without_running_task_twice(
    tmp_path,
) -> None:
    calls = 0

    async def runner(task, slot, session):
        nonlocal calls
        _ = task, slot, session
        calls += 1
        return {"summary": "done"}

    orchestrator = SubagentOrchestrator(
        store=SubagentStore(tmp_path / "subagents.sqlite3"),
        snapshot=_snapshot(),
        root_session_id="root-1",
        ticket_id=4001,
        runner=runner,
    )

    first = orchestrator.spawn(
        slot_id=" reader ", objective=" Inspect logs ", idempotency_key=" logs "
    )
    duplicate = orchestrator.spawn(
        slot_id="reader", objective="different objective", idempotency_key="logs"
    )
    await orchestrator.wait((first.id,), timeout_seconds=1)

    assert duplicate.id == first.id
    assert first.objective == "Inspect logs"
    assert first.idempotency_key == "logs"
    assert calls == 1


def test_spawn_rejects_blank_or_oversized_control_fields(tmp_path) -> None:
    async def runner(task, slot, session):
        _ = task, slot, session
        return {"summary": "done"}

    orchestrator = SubagentOrchestrator(
        store=SubagentStore(tmp_path / "subagents.sqlite3"),
        snapshot=_snapshot(),
        root_session_id="root-1",
        ticket_id=4001,
        runner=runner,
    )

    with pytest.raises(ValueError, match="slot_id"):
        orchestrator.spawn(slot_id=" ", objective="read", idempotency_key="read")
    with pytest.raises(ValueError, match="objective"):
        orchestrator.spawn(slot_id="reader", objective=" ", idempotency_key="read")
    with pytest.raises(ValueError, match="idempotency_key"):
        orchestrator.spawn(slot_id="reader", objective="read", idempotency_key="x" * 129)


@pytest.mark.asyncio
async def test_immediate_cancel_always_persists_cancelled_terminal_state(tmp_path) -> None:
    async def runner(task, slot, session):
        _ = task, slot, session
        await asyncio.Event().wait()
        return {"summary": "unreachable"}

    store = SubagentStore(tmp_path / "subagents.sqlite3")
    orchestrator = SubagentOrchestrator(
        store=store,
        snapshot=_snapshot(),
        root_session_id="root-1",
        ticket_id=4001,
        runner=runner,
    )
    task = orchestrator.spawn(
        slot_id="reader", objective="read", idempotency_key="read"
    )

    cancelled = await orchestrator.cancel(task.id)

    assert cancelled.status is SubagentTaskStatus.CANCELLED
    assert store.get(task.id).status is SubagentTaskStatus.CANCELLED


@pytest.mark.asyncio
async def test_task_waiting_for_child_pool_remains_queued(tmp_path) -> None:
    snapshot = replace(_snapshot(), subagent_max_parallel=1)
    first_started = asyncio.Event()
    release = asyncio.Event()

    async def runner(task, slot, session):
        _ = slot, session
        if task.id:
            first_started.set()
        await release.wait()
        return {"summary": "done"}

    store = SubagentStore(tmp_path / "subagents.sqlite3")
    orchestrator = SubagentOrchestrator(
        store=store,
        snapshot=snapshot,
        root_session_id="root-1",
        ticket_id=4001,
        runner=runner,
    )
    first = orchestrator.spawn(
        slot_id="reader", objective="one", idempotency_key="one"
    )
    second = orchestrator.spawn(
        slot_id="logs", objective="two", idempotency_key="two"
    )
    await asyncio.wait_for(first_started.wait(), timeout=1)

    assert store.get(first.id).status is SubagentTaskStatus.RUNNING
    assert store.get(second.id).status is SubagentTaskStatus.QUEUED

    release.set()
    await orchestrator.wait((first.id, second.id), timeout_seconds=1)
