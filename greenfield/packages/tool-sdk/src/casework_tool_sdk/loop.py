"""Bounded Tool Loop independent of model/provider implementation."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from casework_tool_sdk.registry import (
    ToolExecutionContext,
    ToolExecutionError,
    ToolNotFoundError,
    ToolRegistry,
)
from contracts.tool import ToolResult, ToolResultStatus


@dataclass(frozen=True, slots=True)
class ToolLoopResult:
    results: tuple[ToolResult, ...]
    termination_reason: str
    rounds: int


class ToolLoop:
    """Execute model-selected tool calls with a strict per-run round budget."""

    def __init__(
        self,
        *,
        registry: ToolRegistry,
        max_rounds: int = 8,
        max_retries_per_call: int = 1,
    ) -> None:
        if max_rounds < 1:
            raise ValueError("max_rounds must be positive")
        if max_retries_per_call < 0 or max_retries_per_call > 3:
            raise ValueError("max_retries_per_call must be between 0 and 3")
        self._registry = registry
        self._max_rounds = max_rounds
        self._max_retries_per_call = max_retries_per_call

    async def run(
        self,
        *,
        calls: Iterable[tuple[str, dict[str, Any]]],
        context: ToolExecutionContext,
    ) -> ToolLoopResult:
        results: list[ToolResult] = []
        rounds = 0
        for name, arguments in calls:
            retries = 0
            while True:
                if rounds >= self._max_rounds:
                    return ToolLoopResult(
                        results=tuple(results),
                        termination_reason="max_rounds",
                        rounds=rounds,
                    )
                try:
                    result = await self._registry.execute(
                        name, arguments, context=context
                    )
                except (ToolExecutionError, ToolNotFoundError) as error:
                    result = ToolResult(
                        status=ToolResultStatus.FAILURE,
                        summary=str(error),
                        observed_at=datetime.now(UTC),
                    )
                results.append(result)
                rounds += 1
                if not result.retryable or retries >= self._max_retries_per_call:
                    break
                retries += 1
        return ToolLoopResult(
            results=tuple(results),
            termination_reason="calls_exhausted",
            rounds=rounds,
        )
