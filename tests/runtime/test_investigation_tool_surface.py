from __future__ import annotations

from typing import Any, Callable

import pytest

from zd_agent.config import GuardrailsConfig
from zd_agent.runtime.agent_loop import agent_loop
from zd_agent.runtime.guardrails import Guardrails
from zd_agent.runtime.llm.types import (
    LLMProvider,
    LLMResponse,
    ToolCallRequest,
    ToolSchema,
)
from zd_agent.runtime.messages import MessageStore
from zd_agent.session.agent_session import AgentSession, TicketContext
from zd_agent.tools.base import Tool, ToolRegistry, ToolResult


class _StateTool(Tool):
    description = "state transition"
    parameters = {"type": "object", "properties": {}}

    def __init__(self, name: str, mutate: Callable[[AgentSession], None]) -> None:
        self.name = name
        self._mutate = mutate

    async def execute(self, args: dict[str, Any], session: AgentSession) -> ToolResult:
        self._mutate(session)
        return ToolResult(output="ok")


class _SurfaceProvider(LLMProvider):
    def __init__(self) -> None:
        self.surfaces: list[set[str]] = []

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[ToolSchema] | None = None,
        temperature: float = 0.2,
    ) -> LLMResponse:
        del messages, temperature
        self.surfaces.append({tool.name for tool in tools or []})
        round_number = len(self.surfaces)
        if round_number == 1:
            call = ToolCallRequest(id="intake", name="zendesk_get_ticket", arguments="{}")
        elif round_number == 2:
            call = ToolCallRequest(id="classify", name="classify_ticket", arguments="{}")
        elif round_number == 3:
            call = ToolCallRequest(id="plan", name="plan_investigation", arguments="{}")
        else:
            return LLMResponse(
                content="dry-run complete",
                tool_calls=[],
                finish_reason="stop",
                prompt_tokens=1,
                completion_tokens=1,
                raw={},
            )
        return LLMResponse(
            content=None,
            tool_calls=[call],
            finish_reason="tool_calls",
            prompt_tokens=1,
            completion_tokens=1,
            raw={},
        )

    def format_tool_result(
        self,
        tool_call_id: str,
        name: str,
        content: str,
    ) -> dict[str, Any]:
        return {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "name": name,
            "content": content,
        }


@pytest.mark.asyncio
async def test_agent_loop_filters_provider_tool_surface_after_each_phase() -> None:
    provider = _SurfaceProvider()
    store = MessageStore(provider)
    store.append_system("system")
    store.append_user("ticket")
    registry = ToolRegistry()
    registry.register(
        _StateTool(
            "zendesk_get_ticket",
            lambda session: setattr(session, "context", TicketContext(ticket_id=1)),
        )
    )
    registry.register(
        _StateTool(
            "classify_ticket",
            lambda session: setattr(session, "ticket_type", "product_usage"),
        )
    )

    def _plan(session: AgentSession) -> None:
        session.investigation_route = "fast"
        session.investigation_plan = {
            "route": "fast",
            "evidence_lanes": ["authoritative_docs"],
            "independent_lanes": [],
        }

    registry.register(_StateTool("plan_investigation", _plan))
    session = AgentSession(ticket_id=1, dry_run=True)
    session.investigation_policy_enabled = True
    schemas = [
        ToolSchema(name=name, description=name, parameters={"type": "object"})
        for name in (
            "zendesk_get_ticket",
            "zendesk_get_attachments",
            "classify_ticket",
            "plan_investigation",
            "official_docs_search",
            "official_docs_read",
            "sql_query",
            "signoz_search_logs",
            "run_subagents_parallel",
            "zendesk_public_reply",
        )
    ]

    _ = [
        event
        async for event in agent_loop(
            session,
            provider,
            store,
            registry,
            Guardrails(GuardrailsConfig(max_rounds=10)),
            schemas,
        )
    ]

    assert provider.surfaces[0] == {"zendesk_get_ticket"}
    assert provider.surfaces[1] == {
        "zendesk_get_ticket",
        "zendesk_get_attachments",
        "classify_ticket",
    }
    assert provider.surfaces[2] == {
        "zendesk_get_ticket",
        "zendesk_get_attachments",
        "plan_investigation",
    }
    assert {
        "official_docs_search",
        "official_docs_read",
        "zendesk_public_reply",
    } <= provider.surfaces[3]
    assert "sql_query" not in provider.surfaces[3]
    assert "signoz_search_logs" not in provider.surfaces[3]
    assert "run_subagents_parallel" not in provider.surfaces[3]
