from datetime import UTC, datetime

import pytest

from casework_agent_core.domain import ActiveQuestion, TicketSnapshot
from casework_agent_core.memory import WorkingMemory
from casework_agent_core.skills import LoadedSkill
from casework_agent_core.subagents import SubagentReport, SubagentTask
from casework_agent_core.todo import TodoPlan
from casework_sandbox.runtime import SandboxAgent
from casework_tool_sdk.registry import ToolExecutionContext, ToolRegistry
from contracts.evidence import Evidence
from contracts.ticket import TicketMessage
from contracts.todo import Todo
from contracts.tool import ToolDefinition, ToolResult, ToolResultStatus


def _snapshot(ticket_id: int) -> TicketSnapshot:
    event_id = f"evt-{ticket_id}"
    return TicketSnapshot(
        ticket_id=ticket_id,
        status="open",
        requester_id=1,
        assignee_id=2,
        messages=(
            TicketMessage(
                event_id=event_id,
                author_origin="customer",
                public=True,
                body="Investigate this ticket",
                created_at=datetime.now(UTC),
            ),
        ),
        locale="en-US",
        latest_customer_cursor=event_id,
        complete=True,
        fetched_at=datetime.now(UTC),
    )


class _Reader:
    def __init__(self, snapshots: dict[int, TicketSnapshot]) -> None:
        self.snapshots = snapshots

    async def fetch_ticket(
        self, ticket_id: int, *, expected_event_id: str | None = None
    ) -> TicketSnapshot:
        del expected_event_id
        return self.snapshots[ticket_id]


class _RootModel:
    async def plan(
        self,
        snapshot: TicketSnapshot,
        question: ActiveQuestion,
        skill: LoadedSkill,
        available_tools: tuple[str, ...],
    ) -> TodoPlan:
        del question, skill
        return TodoPlan(
            items=[
                Todo(
                    task_id=f"lookup-{snapshot.ticket_id}",
                    goal="Find evidence",
                    suggested_tools=(available_tools[0],),
                    completion_condition="evidence found",
                )
            ]
        )

    async def compose_answer(
        self,
        snapshot: TicketSnapshot,
        question: ActiveQuestion,
        evidence: tuple[Evidence, ...],
    ) -> str:
        del snapshot, question, evidence
        return "The evidence was reviewed."

    async def repair_tool_call(
        self,
        snapshot: TicketSnapshot,
        question: ActiveQuestion,
        todo: Todo,
        failed_tool_name: str,
        failure_summary: str,
        available_tools: tuple[str, ...],
    ) -> tuple[str, dict[str, object]] | None:
        del snapshot, question, todo, failed_tool_name, failure_summary, available_tools
        return None


class _Tool:
    definition = ToolDefinition(
        name="lookup",
        description="Look up evidence",
        input_schema={"type": "object"},
    )

    async def __call__(
        self,
        arguments: dict[str, object],
        context: ToolExecutionContext,
    ) -> ToolResult:
        del arguments
        return ToolResult(
            status=ToolResultStatus.SUCCESS,
            summary=(
                "raw_secret=do-not-copy hidden_reasoning=never-persist-this"
            ),
            data={"raw_secret": "do-not-copy"},
            evidence_id=f"ev-{context.ticket_id}",
            observed_at=datetime.now(UTC),
        )


class _NoEvidenceChild:
    async def dispatch(self, slot_id: str, task: SubagentTask) -> SubagentReport:
        del slot_id
        return SubagentReport(task_id=task.task_id, summary="No evidence")

    async def dispatch_many(
        self, work: tuple[tuple[str, SubagentTask], ...]
    ) -> tuple[SubagentReport, ...]:
        return tuple([await self.dispatch(slot_id, task) for slot_id, task in work])


class _ChildPlanModel(_RootModel):
    async def plan(
        self,
        snapshot: TicketSnapshot,
        question: ActiveQuestion,
        skill: LoadedSkill,
        available_tools: tuple[str, ...],
    ) -> TodoPlan:
        del snapshot, question, skill, available_tools
        return TodoPlan(
            items=[
                Todo(
                    task_id="child-lookup",
                    goal="Ask the child to find evidence",
                    assigned_to="logs",
                    completion_condition="evidence found",
                )
            ]
        )


def _tools() -> ToolRegistry:
    tools = ToolRegistry()
    tools.register(_Tool.definition, _Tool())
    return tools


@pytest.mark.asyncio
async def test_each_sandbox_run_gets_isolated_working_memory() -> None:
    agent = SandboxAgent(
        zendesk=_Reader({1: _snapshot(1), 2: _snapshot(2)}),
        model=_RootModel(),
        tools=_tools(),
    )

    first = await agent.run(ticket_id=1, session_id="session-1")
    second = await agent.run(ticket_id=2, session_id="session-2")

    assert first.telemetry is not None
    assert second.telemetry is not None
    assert first.telemetry.working_memory_fact_keys == ("tool.lookup-1",)
    assert second.telemetry.working_memory_fact_keys == ("tool.lookup-2",)
    assert first.telemetry.working_memory_fact_count == 1
    assert second.telemetry.working_memory_fact_count == 1
    assert first.telemetry.context_checkpoint is not None
    assert second.telemetry.context_checkpoint is not None
    assert first.telemetry.context_checkpoint.working_memory_fact_count == 1
    assert second.telemetry.context_checkpoint.working_memory_fact_count == 1


@pytest.mark.asyncio
async def test_child_evidence_is_required_before_entering_working_memory() -> None:
    agent = SandboxAgent(
        zendesk=_Reader({1: _snapshot(1)}),
        model=_ChildPlanModel(),
        tools=_tools(),
        subagent_orchestrator=_NoEvidenceChild(),
    )

    outcome = await agent.run(ticket_id=1, session_id="session-1")

    assert outcome.kind == "failed"
    assert "evidence" in (outcome.reason or "")


@pytest.mark.asyncio
async def test_working_memory_projection_never_contains_raw_tool_payload() -> None:
    agent = SandboxAgent(
        zendesk=_Reader({1: _snapshot(1)}),
        model=_RootModel(),
        tools=_tools(),
    )

    outcome = await agent.run(ticket_id=1, session_id="session-1")
    serialized = outcome.model_dump_json()

    assert "raw_secret" not in serialized
    assert "do-not-copy" not in serialized
    assert "hidden_reasoning" not in serialized
    assert outcome.telemetry is not None
    assert outcome.telemetry.working_memory_fact_keys == ("tool.lookup-1",)


def test_working_memory_rejects_unbound_facts() -> None:
    memory = WorkingMemory()

    with pytest.raises(ValueError, match="evidence"):
        memory.record(key="guess", value="unbound", evidence_ids=())
