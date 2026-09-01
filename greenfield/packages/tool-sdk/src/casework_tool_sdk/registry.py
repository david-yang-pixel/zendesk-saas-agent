"""Typed registry for investigation tools available to every Agent session."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from contracts.tool import ToolDefinition, ToolResult


@dataclass(frozen=True, slots=True)
class ToolExecutionContext:
    """Stable run identity passed to root and child investigation tools."""

    ticket_id: int
    session_id: str
    root_session_id: str


class ToolHandler(Protocol):
    def __call__(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult | Awaitable[ToolResult]: ...


class ToolNotFoundError(LookupError):
    """Raised when a model asks for a tool that is not registered."""


class ToolExecutionError(RuntimeError):
    """Raised when an investigation tool returns an invalid result."""


class ToolRegistry:
    """One shared registry; ``for_session`` intentionally does not filter tools.

    Orchestration and delivery are outside this registry.  Every registered
    business/investigation tool is therefore visible to root and depth-one
    child sessions, as required by the casework design.
    """

    def __init__(self) -> None:
        self._definitions: dict[str, ToolDefinition] = {}
        self._handlers: dict[str, ToolHandler] = {}

    def register(self, definition: ToolDefinition, handler: ToolHandler) -> None:
        if definition.name in self._definitions:
            raise ValueError(f"tool already registered: {definition.name}")
        self._definitions[definition.name] = definition
        self._handlers[definition.name] = handler

    def for_session(self, *, kind: str) -> ToolRegistry:
        if kind not in {"root", "subagent"}:
            raise ValueError("tool session kind must be root or subagent")
        return self

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._definitions))

    def definitions(self) -> tuple[ToolDefinition, ...]:
        return tuple(self._definitions[name] for name in self.names())

    async def execute(
        self,
        name: str,
        arguments: Mapping[str, Any],
        *,
        context: ToolExecutionContext,
    ) -> ToolResult:
        try:
            handler = self._handlers[name]
        except KeyError as error:
            raise ToolNotFoundError(name) from error
        if not isinstance(arguments, dict):
            raise ToolExecutionError("tool arguments must be a JSON object")
        try:
            value = handler(dict(arguments), context)
            result = await value if inspect.isawaitable(value) else value
            return ToolResult.model_validate(result)
        except ToolExecutionError:
            raise
        except Exception as error:
            raise ToolExecutionError(f"tool {name} failed") from error
