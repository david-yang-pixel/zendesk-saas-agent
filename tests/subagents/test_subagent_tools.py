from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from zd_agent.casework.adapters.evidence import tool_evidence
from zd_agent.runtime.config_snapshot import (
    CaseworkRuntimeSnapshot,
    LLMRuntimeSnapshot,
    SubagentSlotRuntimeSnapshot,
)
from zd_agent.session.agent_session import AgentSession
from zd_agent.subagents.orchestrator import SubagentOrchestrator
from zd_agent.subagents.store import SubagentStore
from zd_agent.subagents.tools import create_subagent_control_tools


def _snapshot() -> CaseworkRuntimeSnapshot:
    llm = LLMRuntimeSnapshot(
        provider="openai",
        model="child-model",
        reasoning_effort="low",
        enable_vision_input=False,
        temperature=0.0,
        base_url=None,
        timeout=60,
        max_tokens=4096,
        context_window_tokens=128_000,
        api_key="child-secret",
    )
    return CaseworkRuntimeSnapshot(
        main_llm=llm,
        subagents_enabled=True,
        subagent_slots=(SubagentSlotRuntimeSnapshot(id="reader", label="Reader", llm=llm),),
        subagent_max_parallel=1,
        subagent_max_tasks_per_run=2,
        subagent_task_timeout_seconds=30,
        tool_names=("read",),
        config_revision="revision-1",
        config_digest="digest-1",
    )


@pytest.mark.asyncio
async def test_root_control_tools_spawn_wait_and_return_child_result_metadata(tmp_path) -> None:
    evidence = tool_evidence(
        call_id="child-docs",
        tool_name="official_docs_search",
        args={"query": "timeout"},
        allowed_target_arguments=frozenset({"query"}),
        observed_at=datetime(2026, 8, 31, tzinfo=timezone.utc),
        success=True,
        output="Published behavior",
        error="",
    )

    async def runner(task, slot, session):
        _ = task, slot, session
        return {
            "summary": "Child inspected current evidence.",
            "evidence": [evidence.model_dump(mode="json")],
        }

    orchestrator = SubagentOrchestrator(
        store=SubagentStore(tmp_path / "subagents.sqlite3"),
        snapshot=_snapshot(),
        root_session_id="root-1",
        ticket_id=4001,
        runner=runner,
    )
    tools = {tool.name: tool for tool in create_subagent_control_tools(orchestrator)}
    root = AgentSession(ticket_id=4001, session_id="root-1")

    spawned = await tools["subagent_spawn"].execute(
        {
            "slot_id": "reader",
            "objective": "Inspect current logs",
            "idempotency_key": "logs",
        },
        root,
    )
    task_id = json.loads(spawned.output)["task_id"]
    waited = await tools["subagent_wait"].execute(
        {"task_ids": [task_id], "timeout_seconds": 1}, root
    )
    payload = json.loads(waited.output)

    assert spawned.success is True
    assert payload["tasks"][0]["status"] == "completed"
    assert payload["tasks"][0]["result"]["summary"].startswith("Child")
    assert waited.metadata["subagent_task_ids"] == [task_id]
    assert waited.metadata["subagent_results"][0]["evidence"][0]["evidence_id"] == "child-docs"
    delegated = waited.metadata["casework_evidence_items"][0]
    assert delegated["evidence_id"] == f"subagent:{task_id}:child-docs"
    assert payload["tasks"][0]["ledger_evidence_ids"] == [
        f"subagent:{task_id}:child-docs"
    ]
    assert tools["subagent_spawn"].root_only is True


@pytest.mark.asyncio
async def test_subagent_status_rejects_task_from_another_root(tmp_path) -> None:
    async def runner(task, slot, session):
        _ = task, slot, session
        return {"summary": "done"}

    store = SubagentStore(tmp_path / "subagents.sqlite3")
    other = store.create(
        root_session_id="root-other",
        parent_session_id="root-other",
        ticket_id=4002,
        slot_id="reader",
        objective="other",
        idempotency_key="other",
        config_digest="digest-1",
    )
    orchestrator = SubagentOrchestrator(
        store=store,
        snapshot=_snapshot(),
        root_session_id="root-1",
        ticket_id=4001,
        runner=runner,
    )
    tools = {tool.name: tool for tool in create_subagent_control_tools(orchestrator)}

    result = await tools["subagent_status"].execute(
        {"task_id": other.id}, AgentSession(ticket_id=4001)
    )

    assert result.success is False
    assert result.error == "subagent task not found"
