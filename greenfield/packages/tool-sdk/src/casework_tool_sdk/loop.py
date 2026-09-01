"""Bounded Tool Loop independent of model/provider implementation."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from casework_tool_sdk.registry import ToolExecutionContext, ToolRegistry
from contracts.tool import ToolResult


@dataclass(frozen=True, slots=True)
class ToolLoopResult:
    results: tuple[ToolResult, ...]
    termination_reason: str
    rounds: int


class ToolLoop:
    """Execute model-selected tool calls with a strict per-run round budget."""

    def __init__(self, *, registry: ToolRegistry, max_rounds: int = 8) -> None:
        if max_rounds < 1:
            raise ValueError("max_rounds must be positive")
        self._registry = registry
        self._max_rounds = max_rounds

    async def run(
        self,
        *,
        calls: Iterable[tuple[str, dict[str, Any]]],
        context: ToolExecutionContext,
    ) -> ToolLoopResult:
        results: list[ToolResult] = []
        for rounds, (name, arguments) in enumerate(calls, start=1):
            if rounds > self._max_rounds:
                return ToolLoopResult(
                    results=tuple(results),
                    termination_reason="max_rounds",
                    rounds=self._max_rounds,
                )
            results.append(
                await self._registry.execute(name, arguments, context=context)
            )
        return ToolLoopResult(
            results=tuple(results),
            termination_reason="calls_exhausted",
            rounds=len(results),
        )
