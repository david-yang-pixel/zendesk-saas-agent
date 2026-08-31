"""Provider-neutral model/tool loop shared by root and child casework sessions."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Generic, TypeVar, cast

from zd_agent.runtime.llm.types import (
    LLMProvider,
    LLMResponse,
    ToolCallRequest,
    ToolSchema,
)


TerminalT = TypeVar("TerminalT")
CallResultT = TypeVar("CallResultT")
ResolvedT = TypeVar("ResolvedT")
Message = dict[str, Any]


@dataclass(frozen=True)
class ContinueToolLoop:
    """Terminal handler response requesting another model round."""

    message: str


async def _resolve(value: ResolvedT | Awaitable[ResolvedT]) -> ResolvedT:
    if inspect.isawaitable(value):
        return await cast(Awaitable[ResolvedT], value)
    return cast(ResolvedT, value)


class CaseworkToolLoop(Generic[TerminalT, CallResultT]):
    """Run repeated model/tool rounds while policy remains in injected hooks."""

    def __init__(
        self,
        *,
        provider: LLMProvider,
        schemas: list[ToolSchema] | None,
        prepare_messages: Callable[
            [list[Message], list[ToolSchema] | None],
            list[Message] | Awaitable[list[Message]],
        ],
        validate_calls: Callable[
            [list[ToolCallRequest], set[str]], dict[str, dict[str, Any]]
        ],
        execute_calls: Callable[
            [list[ToolCallRequest], dict[str, dict[str, Any]], int],
            Awaitable[list[CallResultT]],
        ],
        format_call_result: Callable[[ToolCallRequest, CallResultT], Message],
        handle_terminal: Callable[
            [list[Message], str],
            TerminalT
            | ContinueToolLoop
            | Awaitable[TerminalT | ContinueToolLoop],
        ],
        record_response: Callable[[LLMResponse], None],
        assistant_message: Callable[[LLMResponse], Message],
        temperature: float = 0.0,
    ) -> None:
        self._provider = provider
        self._schemas = schemas
        self._prepare_messages = prepare_messages
        self._validate_calls = validate_calls
        self._execute_calls = execute_calls
        self._format_call_result = format_call_result
        self._handle_terminal = handle_terminal
        self._record_response = record_response
        self._assistant_message = assistant_message
        self._temperature = temperature

    async def run(self, messages: list[Message]) -> TerminalT:
        active_messages = list(messages)
        used_ids: set[str] = set()
        round_index = 0
        while True:
            round_index += 1
            active_messages = await _resolve(
                self._prepare_messages(active_messages, self._schemas)
            )
            response = await self._provider.chat(
                active_messages,
                tools=self._schemas,
                temperature=self._temperature,
            )
            self._record_response(response)
            active_messages.append(self._assistant_message(response))
            if not response.tool_calls:
                outcome = await _resolve(
                    self._handle_terminal(active_messages, response.content or "")
                )
                if isinstance(outcome, ContinueToolLoop):
                    active_messages.append(
                        {"role": "user", "content": outcome.message}
                    )
                    continue
                return outcome

            validated_args = self._validate_calls(response.tool_calls, used_ids)
            results = await self._execute_calls(
                response.tool_calls,
                validated_args,
                round_index,
            )
            for call, result in zip(response.tool_calls, results, strict=True):
                active_messages.append(self._format_call_result(call, result))
                used_ids.add(call.id)
