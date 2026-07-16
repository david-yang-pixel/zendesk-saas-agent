from __future__ import annotations

from typing import Any

import pytest

from zd_agent.config import GuardrailsConfig
from zd_agent.runtime.agent_loop import agent_loop
from zd_agent.runtime.events import LoopFinished
from zd_agent.runtime.guardrails import Guardrails
from zd_agent.runtime.llm.types import LLMResponse, ToolSchema
from zd_agent.runtime.messages import MessageStore
from zd_agent.session.agent_session import AgentSession, SessionStatus
from zd_agent.tools.base import ToolRegistry


class _FinalAnswerProvider:
    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[ToolSchema] | None = None,
        temperature: float = 0.2,
    ) -> LLMResponse:
        return LLMResponse(
            content="Investigation complete.",
            tool_calls=[],
            finish_reason="stop",
            prompt_tokens=10,
            completion_tokens=2,
            raw={"role": "assistant", "content": "Investigation complete."},
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


async def _run_final_answer(session: AgentSession) -> list[LoopFinished]:
    provider = _FinalAnswerProvider()
    store = MessageStore(provider)
    store.append_system("system")
    store.append_user("investigate ticket")
    guardrails = Guardrails(
        GuardrailsConfig(
            max_rounds=3,
            timeout_seconds=600,
            max_tokens_per_session=200_000,
        )
    )

    events = [
        event
        async for event in agent_loop(
            session,
            provider,
            store,
            ToolRegistry(),
            guardrails,
            [],
        )
    ]
    return [event for event in events if isinstance(event, LoopFinished)]


@pytest.mark.asyncio
async def test_production_run_without_customer_delivery_cannot_complete() -> None:
    session = AgentSession(ticket_id=3461, dry_run=False)

    finished = await _run_final_answer(session)

    assert session.reply_sent is False
    assert session.status is SessionStatus.FAILED
    assert len(finished) == 1
    assert finished[0].status == "error"


@pytest.mark.asyncio
async def test_dry_run_with_draft_may_complete_without_sending_reply() -> None:
    session = AgentSession(ticket_id=3462, dry_run=True)
    session.reply_draft = "Suggested customer response"

    finished = await _run_final_answer(session)

    assert session.reply_sent is False
    assert session.status is SessionStatus.COMPLETED
    assert len(finished) == 1
    assert finished[0].status == "completed"


@pytest.mark.asyncio
async def test_production_run_with_public_reply_may_complete() -> None:
    session = AgentSession(ticket_id=3463, dry_run=False)
    session.reply_sent = True

    finished = await _run_final_answer(session)

    assert session.status is SessionStatus.COMPLETED
    assert len(finished) == 1
    assert finished[0].status == "completed"
