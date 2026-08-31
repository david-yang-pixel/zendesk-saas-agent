from __future__ import annotations

import json

import pytest

from zd_agent.memory.models import MemoryKind
from zd_agent.memory.store import MemoryStore
from zd_agent.memory.tools import create_memory_tools
from zd_agent.session.agent_session import AgentSession


@pytest.mark.asyncio
async def test_memory_search_tool_labels_results_as_historical_guidance(tmp_path) -> None:
    store = MemoryStore(tmp_path / "memory.sqlite3")
    entry = store.propose(
        kind=MemoryKind.SEMANTIC,
        title="Provider timeout",
        content="Historical timeout handling note.",
        proposed_by="admin",
    )
    store.approve(entry.id, reviewer="admin")
    tools = {tool.name: tool for tool in create_memory_tools(store, "root-1")}

    result = await tools["memory_search"].execute(
        {"query": "timeout"}, AgentSession(ticket_id=4001)
    )
    payload = json.loads(result.output)

    assert result.success is True
    assert payload["authority"] == "historical_guidance"
    assert payload["may_support_current_claims"] is False
    assert payload["results"][0]["id"] == entry.id


@pytest.mark.asyncio
async def test_memory_propose_tool_never_auto_approves(tmp_path) -> None:
    store = MemoryStore(tmp_path / "memory.sqlite3")
    tools = {tool.name: tool for tool in create_memory_tools(store, "root-1")}

    result = await tools["memory_propose"].execute(
        {
            "kind": "episodic",
            "title": "Potential reusable finding",
            "content": "Needs human review.",
            "source_ticket_id": 4001,
            "evidence_references": ["ticket:4001"],
        },
        AgentSession(ticket_id=4001, session_id="child-a"),
    )
    payload = json.loads(result.output)

    assert result.success is True
    assert payload["status"] == "proposed"
    assert store.search("reusable") == ()
    assert tools["memory_propose"].is_write_operation is True
