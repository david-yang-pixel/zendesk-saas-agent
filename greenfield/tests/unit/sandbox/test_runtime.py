from datetime import UTC, datetime

import pytest

from casework_agent_core.domain import TicketComment, TicketSnapshot
from casework_sandbox.fakes import FakeModel, FakeZendesk
from casework_sandbox.runtime import SandboxAgent
from casework_tool_sdk.registry import ToolExecutionContext, ToolRegistry
from contracts.outcome import OutcomeKind
from contracts.tool import ToolDefinition, ToolResult, ToolResultStatus


class WorkspaceTool:
    definition = ToolDefinition(
        name="workspace_lookup",
        description="Find a workspace",
        input_schema={"type": "object"},
    )

    async def __call__(
        self,
        arguments: dict[str, object],
        context: ToolExecutionContext,
    ) -> ToolResult:
        return ToolResult(
            status=ToolResultStatus.SUCCESS,
            summary="Found the Dify workspace",
            data={"workspace_id": "ws-dify"},
            evidence_id="ev-ws-dify",
            observed_at=datetime.now(UTC),
            targets=("ws-dify",),
        )


@pytest.mark.asyncio
async def test_sandbox_runtime_fetches_by_ticket_plans_calls_tool_and_returns_outcome(
) -> None:
    zendesk = FakeZendesk(
        TicketSnapshot(
            ticket_id=3726,
            subject="Workspace lookup",
            requester_email="customer@example.com",
            locale="en-US",
            status="open",
            assignee="SaaS",
            tags=("saas",),
            comments=(
                TicketComment(
                    comment_id="c-01",
                    body="Which workspace is affected?",
                    public=True,
                    author_origin="customer",
                    created_at=datetime.now(UTC),
                    event_id="evt-3726",
                ),
            ),
            latest_customer_event_id="evt-3726",
        )
    )
    tools = ToolRegistry()
    tools.register(WorkspaceTool.definition, WorkspaceTool())
    model = FakeModel(answer="The affected workspace is Dify's workspace.")

    outcome = await SandboxAgent(
        zendesk=zendesk,
        model=model,
        tools=tools,
    ).run(ticket_id=3726, session_id="session-3726")

    assert zendesk.fetched_ticket_ids == [3726]
    assert model.planned_ticket_ids == [3726]
    assert model.answer_calls == 1
    assert outcome.kind is OutcomeKind.ANSWER
    assert outcome.answer == "The affected workspace is Dify's workspace."
    assert outcome.evidence_ids == ("ev-ws-dify",)
    assert outcome.todo_summary.completed == 1
