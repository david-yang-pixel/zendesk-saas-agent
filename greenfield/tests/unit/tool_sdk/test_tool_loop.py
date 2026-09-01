from datetime import UTC, datetime

import pytest

from casework_tool_sdk.loop import ToolLoop
from casework_tool_sdk.registry import (
    ToolExecutionContext,
    ToolExecutionError,
    ToolRegistry,
)
from contracts.tool import ToolDefinition, ToolResult, ToolResultStatus


class FakeLookupTool:
    definition = ToolDefinition(
        name="workspace_lookup",
        description="Find workspaces for a customer",
        input_schema={"type": "object"},
    )

    async def __call__(
        self,
        arguments: dict[str, object],
        context: ToolExecutionContext,
    ) -> ToolResult:
        return ToolResult(
            status=ToolResultStatus.SUCCESS,
            summary=f"workspace for ticket {context.ticket_id}",
            data={"workspace_id": "ws-01", "query": str(arguments["query"])},
            evidence_id="ev-workspace",
            observed_at=datetime.now(UTC),
            targets=("ws-01",),
        )


@pytest.mark.asyncio
async def test_registered_investigation_tools_are_shared_by_root_and_child() -> None:
    registry = ToolRegistry()
    registry.register(FakeLookupTool.definition, FakeLookupTool())

    root = registry.for_session(kind="root")
    child = registry.for_session(kind="subagent")

    assert root.names() == child.names() == ("workspace_lookup",)
    result = await child.execute(
        "workspace_lookup",
        {"query": "Dify"},
        context=ToolExecutionContext(
            ticket_id=3726,
            session_id="child-01",
            root_session_id="root-01",
        ),
    )

    assert result.status is ToolResultStatus.SUCCESS
    assert result.data == {"workspace_id": "ws-01", "query": "Dify"}


@pytest.mark.asyncio
async def test_tool_loop_is_bounded_and_returns_typed_results() -> None:
    registry = ToolRegistry()
    registry.register(FakeLookupTool.definition, FakeLookupTool())
    loop = ToolLoop(registry=registry, max_rounds=1)

    result = await loop.run(
        calls=[
            ("workspace_lookup", {"query": "Dify"}),
            ("workspace_lookup", {"query": "Dify again"}),
        ],
        context=ToolExecutionContext(
            ticket_id=3726,
            session_id="root-01",
            root_session_id="root-01",
        ),
    )

    assert len(result.results) == 1
    assert result.termination_reason == "max_rounds"
    assert result.results[0].evidence_id == "ev-workspace"


@pytest.mark.asyncio
async def test_tool_loop_retries_only_retryable_results_within_round_budget() -> None:
    attempts = 0

    async def flaky_tool(
        arguments: dict[str, object], context: ToolExecutionContext
    ) -> ToolResult:
        nonlocal attempts
        del arguments, context
        attempts += 1
        if attempts == 1:
            return ToolResult(
                status=ToolResultStatus.FAILURE,
                summary="temporary timeout",
                observed_at=datetime.now(UTC),
                retryable=True,
            )
        return ToolResult(
            status=ToolResultStatus.SUCCESS,
            summary="recovered",
            evidence_id="ev-recovered",
            observed_at=datetime.now(UTC),
        )

    registry = ToolRegistry()
    registry.register(FakeLookupTool.definition, flaky_tool)
    result = await ToolLoop(
        registry=registry, max_rounds=3, max_retries_per_call=1
    ).run(
        calls=[("workspace_lookup", {"query": "Dify"})],
        context=ToolExecutionContext(
            ticket_id=3726,
            session_id="root-01",
            root_session_id="root-01",
        ),
    )

    assert attempts == 2
    assert result.rounds == 2
    assert result.results[-1].evidence_id == "ev-recovered"
    assert result.termination_reason == "calls_exhausted"


@pytest.mark.asyncio
async def test_registry_validates_required_typed_arguments_before_execution() -> None:
    registry = ToolRegistry()
    calls: list[dict[str, object]] = []
    definition = ToolDefinition(
        name="typed_lookup",
        description="Typed lookup",
        input_schema={
            "type": "object",
            "required": ["query"],
            "properties": {"query": {"type": "string"}},
        },
    )
    async def typed_handler(
        arguments: dict[str, object], context: ToolExecutionContext
    ) -> ToolResult:
        calls.append(arguments)
        return await FakeLookupTool()(arguments, context)

    registry.register(definition, typed_handler)

    with pytest.raises(ToolExecutionError, match="query"):
        await registry.execute(
            "typed_lookup",
            {},
            context=ToolExecutionContext(
                ticket_id=3726,
                session_id="root-01",
                root_session_id="root-01",
            ),
        )
    assert calls == []
