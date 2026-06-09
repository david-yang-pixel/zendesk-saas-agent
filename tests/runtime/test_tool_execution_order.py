import asyncio
from dataclasses import dataclass
from typing import Any

import pytest

from zd_agent.runtime.agent_loop import _execute_tool_calls
from zd_agent.runtime.llm.types import ToolCallRequest
from zd_agent.runtime.messages import MessageStore
from zd_agent.session.agent_session import AgentSession
from zd_agent.tools.base import Tool, ToolRegistry, ToolResult


class _FakeProvider:
    def format_tool_result(
        self, tool_call_id: str, name: str, content: str,
    ) -> dict[str, Any]:
        return {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "name": name,
            "content": content,
        }


@dataclass
class _ConcurrencyProbe:
    active: int = 0
    max_active: int = 0


class _ProbeTool(Tool):
    name = "probe"
    description = "probe"
    parameters = {"type": "object", "properties": {}}

    def __init__(
        self,
        probe: _ConcurrencyProbe,
        *,
        concurrency_safe: bool,
        name: str = "probe",
    ) -> None:
        self.probe = probe
        self.name = name
        self.concurrency_safe = concurrency_safe

    async def execute(self, args: dict[str, Any], session: AgentSession) -> ToolResult:
        self.probe.active += 1
        self.probe.max_active = max(self.probe.max_active, self.probe.active)
        await asyncio.sleep(0.01)
        self.probe.active -= 1
        return ToolResult(output="ok")


@pytest.mark.asyncio
async def test_unsafe_tool_calls_run_serially() -> None:
    probe = _ConcurrencyProbe()
    registry = ToolRegistry()
    registry.register(_ProbeTool(probe, concurrency_safe=False))
    session = AgentSession(ticket_id=1)
    store = MessageStore(_FakeProvider())
    calls = [
        ToolCallRequest(id="a", name="probe", arguments="{}"),
        ToolCallRequest(id="b", name="probe", arguments="{}"),
    ]

    events = [
        event
        async for event in _execute_tool_calls(calls, registry, session, store, 1)
    ]

    assert len(events) == 2
    assert probe.max_active == 1
    assert len(store.raw) == 2


@pytest.mark.asyncio
async def test_safe_tool_calls_can_run_in_parallel() -> None:
    probe = _ConcurrencyProbe()
    registry = ToolRegistry()
    registry.register(_ProbeTool(probe, concurrency_safe=True))
    session = AgentSession(ticket_id=1)
    store = MessageStore(_FakeProvider())
    calls = [
        ToolCallRequest(id="a", name="probe", arguments="{}"),
        ToolCallRequest(id="b", name="probe", arguments="{}"),
    ]

    events = [
        event
        async for event in _execute_tool_calls(calls, registry, session, store, 1)
    ]

    assert len(events) == 2
    assert probe.max_active == 2
    assert len(store.raw) == 2


@pytest.mark.asyncio
async def test_unsafe_tool_preserves_call_order_before_safe_batch() -> None:
    probe = _ConcurrencyProbe()
    registry = ToolRegistry()
    registry.register(_ProbeTool(probe, concurrency_safe=False, name="unsafe"))
    registry.register(_ProbeTool(probe, concurrency_safe=True, name="safe"))
    session = AgentSession(ticket_id=1)
    store = MessageStore(_FakeProvider())
    calls = [
        ToolCallRequest(id="a", name="unsafe", arguments="{}"),
        ToolCallRequest(id="b", name="safe", arguments="{}"),
    ]

    events = [
        event
        async for event in _execute_tool_calls(calls, registry, session, store, 1)
    ]

    assert [event.tool_name for event in events] == ["unsafe", "safe"]
