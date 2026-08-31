from __future__ import annotations

from typing import Any

import pytest

from zd_agent.casework.adapters.tool_loop import CaseworkToolLoop, ContinueToolLoop
from zd_agent.runtime.llm.types import (
    LLMProvider,
    LLMResponse,
    ToolCallRequest,
    ToolSchema,
)


class SequenceProvider(LLMProvider):
    def __init__(self, responses: list[LLMResponse]) -> None:
        self.responses = list(responses)
        self.calls: list[list[dict[str, Any]]] = []

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[ToolSchema] | None = None,
        temperature: float = 0.2,
    ) -> LLMResponse:
        _ = tools, temperature
        self.calls.append(list(messages))
        return self.responses.pop(0)

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


def _response(
    *, content: str | None = None, calls: list[ToolCallRequest] | None = None
) -> LLMResponse:
    return LLMResponse(
        content=content,
        tool_calls=calls or [],
        finish_reason="tool_calls" if calls else "stop",
        prompt_tokens=3,
        completion_tokens=2,
    )


@pytest.mark.asyncio
async def test_tool_loop_repeats_model_tool_rounds_until_terminal_result() -> None:
    call = ToolCallRequest(id="call-1", name="read", arguments='{"id": 7}')
    provider = SequenceProvider(
        [_response(calls=[call]), _response(content="terminal")]
    )
    executed: list[tuple[int, str]] = []
    recorded: list[int] = []

    async def execute(calls, validated_args, round_index):
        executed.append((round_index, validated_args["call-1"]["id"]))
        return ["evidence"]

    loop = CaseworkToolLoop[str, str](
        provider=provider,
        schemas=[ToolSchema(name="read", description="read", parameters={})],
        prepare_messages=lambda messages, _schemas: messages,
        validate_calls=lambda calls, _used: {
            item.id: {"id": 7} for item in calls
        },
        execute_calls=execute,
        format_call_result=lambda item, result: provider.format_tool_result(
            item.id, item.name, result
        ),
        handle_terminal=lambda _messages, content: content,
        record_response=lambda response: recorded.append(response.prompt_tokens),
        assistant_message=lambda response: {
            "role": "assistant",
            "content": response.content,
            "tool_calls": response.tool_calls,
        },
    )

    result = await loop.run([{"role": "user", "content": "start"}])

    assert result == "terminal"
    assert executed == [(1, 7)]
    assert recorded == [3, 3]
    assert provider.calls[1][-1]["role"] == "tool"


@pytest.mark.asyncio
async def test_tool_loop_can_reject_terminal_output_and_continue() -> None:
    provider = SequenceProvider(
        [_response(content="incomplete"), _response(content="complete")]
    )

    async def terminal(_messages, content):
        if content == "incomplete":
            return ContinueToolLoop("collect more evidence")
        return content

    async def execute(_calls, _validated_args, _round_index):
        return []

    loop = CaseworkToolLoop[str, str](
        provider=provider,
        schemas=[],
        prepare_messages=lambda messages, _schemas: messages,
        validate_calls=lambda _calls, _used: {},
        execute_calls=execute,
        format_call_result=lambda _call, result: {"role": "tool", "content": result},
        handle_terminal=terminal,
        record_response=lambda _response: None,
        assistant_message=lambda response: {
            "role": "assistant",
            "content": response.content,
        },
    )

    result = await loop.run([{"role": "user", "content": "start"}])

    assert result == "complete"
    assert provider.calls[1][-1] == {
        "role": "user",
        "content": "collect more evidence",
    }
