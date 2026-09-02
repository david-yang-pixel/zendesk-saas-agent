from datetime import UTC, datetime

import pytest

from casework_agent_core.domain import ActiveQuestion, TicketComment, TicketSnapshot
from casework_agent_core.skills import LoadedSkill
from casework_agent_core.todo import TodoPlan
from casework_sandbox.fakes import FakeModel, FakeZendesk
from casework_sandbox.runtime import SandboxAgent
from casework_tool_sdk.registry import ToolExecutionContext, ToolRegistry
from contracts.evidence import Evidence
from contracts.outcome import OutcomeKind
from contracts.todo import Todo
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


class RetryWorkspaceTool(WorkspaceTool):
    def __init__(self) -> None:
        self.calls = 0

    async def __call__(
        self,
        arguments: dict[str, object],
        context: ToolExecutionContext,
    ) -> ToolResult:
        self.calls += 1
        if self.calls == 1:
            return ToolResult(
                status=ToolResultStatus.FAILURE,
                summary="temporary timeout",
                observed_at=datetime.now(UTC),
                retryable=True,
            )
        return await super().__call__(arguments, context)


class DependentModel:
    async def plan(
        self,
        snapshot: TicketSnapshot,
        question: ActiveQuestion,
        skill: LoadedSkill,
        available_tools: tuple[str, ...],
    ) -> TodoPlan:
        del snapshot, question, skill
        return TodoPlan(
            items=[
                Todo(
                    task_id="task-01",
                    goal="Find the workspace",
                    suggested_tools=(available_tools[0],),
                    completion_condition="workspace found",
                ),
                Todo(
                    task_id="task-02",
                    goal="Confirm the workspace",
                    dependencies=("task-01",),
                    suggested_tools=(available_tools[0],),
                    completion_condition="workspace confirmed",
                ),
            ]
        )

    async def compose_answer(
        self,
        snapshot: TicketSnapshot,
        question: ActiveQuestion,
        evidence: tuple[Evidence, ...],
    ) -> str:
        del snapshot, question, evidence
        return "The workspace is Dify's workspace."


@pytest.mark.asyncio
async def test_sandbox_runtime_fetches_by_ticket_plans_calls_tool_and_returns_outcome(
) -> None:
    zendesk = FakeZendesk(
        TicketSnapshot(
            ticket_id=3726,
            status="open",
            requester_id=1,
            assignee_id=2,
            tags=("saas",),
            messages=(
                TicketComment(
                    event_id="evt-3726",
                    author_origin="customer",
                    public=True,
                    body="Which workspace is affected?",
                    created_at=datetime.now(UTC),
                ),
            ),
            locale="en-US",
            latest_customer_cursor="evt-3726",
            complete=True,
            fetched_at=datetime.now(UTC),
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
    assert outcome.reply_locale == "en-US"
    assert outcome.snapshot_cursor == "evt-3726"
    assert outcome.delivery_eligible is True
    assert outcome.telemetry is not None
    assert outcome.telemetry.active_question is not None
    assert outcome.telemetry.active_question.original_request == (
        "Which workspace is affected?"
    )
    assert outcome.telemetry.todos[0].status == "completed"
    assert outcome.telemetry.tool_calls[0].summary == "Found the Dify workspace"
    assert outcome.telemetry.tool_calls[0].evidence_ids == ("ev-ws-dify",)
    assert outcome.telemetry.evidence_ids == ("ev-ws-dify",)
    assert outcome.telemetry.context_checkpoint is not None
    assert "data" not in outcome.telemetry.model_dump(mode="json")


@pytest.mark.asyncio
async def test_runtime_does_not_answer_before_dependent_todos_are_executed() -> None:
    snapshot = TicketSnapshot(
        ticket_id=3726,
        status="open",
        requester_id=1,
        assignee_id=2,
        tags=("saas",),
        messages=(
            TicketComment(
                event_id="evt-3726",
                author_origin="customer",
                public=True,
                body="Which workspace is affected?",
                created_at=datetime.now(UTC),
            ),
        ),
        locale="en-US",
        latest_customer_cursor="evt-3726",
        complete=True,
        fetched_at=datetime.now(UTC),
    )
    tools = ToolRegistry()
    tools.register(WorkspaceTool.definition, WorkspaceTool())

    outcome = await SandboxAgent(
        zendesk=FakeZendesk(snapshot),
        model=DependentModel(),
        tools=tools,
    ).run(ticket_id=3726, session_id="session-3726")

    assert outcome.kind is OutcomeKind.ANSWER
    assert outcome.todo_summary.completed == 2


@pytest.mark.asyncio
async def test_runtime_uses_successful_final_attempt_after_bounded_tool_retry() -> None:
    snapshot = TicketSnapshot(
        ticket_id=3726,
        status="open",
        requester_id=1,
        assignee_id=2,
        messages=(
            TicketComment(
                event_id="evt-3726",
                author_origin="customer",
                public=True,
                body="Which workspace is affected?",
                created_at=datetime.now(UTC),
            ),
        ),
        locale="en-US",
        latest_customer_cursor="evt-3726",
        complete=True,
        fetched_at=datetime.now(UTC),
    )
    tool = RetryWorkspaceTool()
    tools = ToolRegistry()
    tools.register(tool.definition, tool)

    outcome = await SandboxAgent(
        zendesk=FakeZendesk(snapshot),
        model=FakeModel(answer="The workspace is Dify's workspace."),
        tools=tools,
    ).run(ticket_id=3726, session_id="session-3726")

    assert tool.calls == 2
    assert outcome.kind is OutcomeKind.ANSWER
    assert outcome.evidence_ids == ("ev-ws-dify",)
