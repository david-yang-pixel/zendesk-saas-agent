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
        self._validate_arguments(self._definitions[name], arguments)
        try:
            value = handler(dict(arguments), context)
            result = await value if inspect.isawaitable(value) else value
            return ToolResult.model_validate(result)
        except ToolExecutionError:
            raise
        except Exception as error:
            raise ToolExecutionError(f"tool {name} failed") from error

    @staticmethod
    def _validate_arguments(
        definition: ToolDefinition, arguments: dict[str, Any]
    ) -> None:
        """Validate the portable JSON-schema subset used by tool manifests."""

        schema = definition.input_schema
        required = schema.get("required", [])
        if isinstance(required, list):
            for name in required:
                if isinstance(name, str) and name not in arguments:
                    raise ToolExecutionError(
                        f"tool {definition.name} requires argument {name}"
                    )
        properties = schema.get("properties", {})
        if not isinstance(properties, dict):
            return
        for name, value in arguments.items():
            expected = properties.get(name)
            if not isinstance(expected, dict):
                continue
            expected_type = expected.get("type")
            if isinstance(expected_type, str) and not ToolRegistry._matches_type(
                value, expected_type
            ):
                raise ToolExecutionError(
                    f"tool {definition.name} argument {name} must be "
                    f"{expected_type}"
                )

    @staticmethod
    def _matches_type(value: Any, expected_type: str) -> bool:
        if expected_type == "string":
            return isinstance(value, str)
        if expected_type == "integer":
            return isinstance(value, int) and not isinstance(value, bool)
        if expected_type == "number":
            return isinstance(value, (int, float)) and not isinstance(value, bool)
        if expected_type == "boolean":
            return isinstance(value, bool)
        if expected_type == "object":
            return isinstance(value, dict)
        if expected_type == "array":
            return isinstance(value, list)
        if expected_type == "null":
            return value is None
        return True
