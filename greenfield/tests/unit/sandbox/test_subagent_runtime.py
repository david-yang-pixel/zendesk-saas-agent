from datetime import UTC, datetime
from pathlib import Path

import pytest

from casework_agent_core.domain import ActiveQuestion, TicketSnapshot
from casework_agent_core.skills import LoadedSkill
from casework_agent_core.subagents import SubagentReport, SubagentTask
from casework_agent_core.todo import TodoPlan
from casework_sandbox.fakes import FakeZendesk
from casework_sandbox.runtime import SandboxAgent
from casework_tool_sdk.registry import ToolRegistry
from contracts.evidence import Evidence
from contracts.runtime import ChildModelBinding, ModelBinding, RuntimeBootstrap
from contracts.ticket import TicketMessage
from contracts.todo import Todo


class ChildPlanModel:
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
                    task_id="child-task",
                    goal="Inspect runtime evidence",
                    assigned_to="logs",
                    required_evidence=("log-id",),
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
        del snapshot, question
        assert tuple(item.evidence_id for item in evidence) == ("child-ev",)
        return "The child investigation found the issue."

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


class ParallelChildPlanModel(ChildPlanModel):
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
                    task_id=f"child-task-{index}",
                    goal=f"Inspect evidence lane {index}",
                    assigned_to="logs",
                    required_evidence=(f"evidence-{index}",),
                    completion_condition="evidence found",
                )
                for index in range(3)
            ]
        )

    async def compose_answer(
        self,
        snapshot: TicketSnapshot,
        question: ActiveQuestion,
        evidence: tuple[Evidence, ...],
    ) -> str:
        del snapshot, question
        assert len(evidence) == 3
        return "The parallel child investigations completed."


class FakeOrchestrator:
    def __init__(self) -> None:
        self.calls: list[tuple[str, SubagentTask]] = []
        self.batches: list[tuple[tuple[str, SubagentTask], ...]] = []

    async def dispatch(self, slot_id: str, task: SubagentTask) -> SubagentReport:
        self.calls.append((slot_id, task))
        evidence_id = (
            "child-ev"
            if task.task_id == "child-task"
            else f"child-ev-{task.task_id.rsplit('-', 1)[-1]}"
        )
        return SubagentReport(
            task_id=task.task_id,
            summary="Child evidence",
            evidence_ids=(evidence_id,),
        )

    async def dispatch_many(
        self, work: tuple[tuple[str, SubagentTask], ...]
    ) -> tuple[SubagentReport, ...]:
        self.batches.append(work)
        return tuple([await self.dispatch(slot_id, task) for slot_id, task in work])


def _bootstrap() -> RuntimeBootstrap:
    return RuntimeBootstrap(
        config_revision="cfg-1",
        root_model=ModelBinding(
            provider="openai", model="root", credential_ref="secret/root"
        ),
        child_models=(
            ChildModelBinding(
                slot_id="logs",
                provider="openai",
                model="child",
                credential_ref="secret/logs",
            ),
        ),
        enabled_skills=("core-casework",),
        enabled_tools=(),
        model_digest="model",
        skill_digest="skill",
        tool_digest="tool",
    )


@pytest.mark.asyncio
async def test_sandbox_agent_dispatches_assigned_todo_to_depth_one_child(
    tmp_path: Path,
) -> None:
    snapshot = TicketSnapshot(
        ticket_id=1,
        status="open",
        requester_id=1,
        assignee_id=2,
        messages=(
            TicketMessage(
                event_id="evt-1",
                author_origin="customer",
                public=True,
                body="Investigate this",
                created_at=datetime.now(UTC),
            ),
        ),
        locale="en-US",
        latest_customer_cursor="evt-1",
        complete=True,
        fetched_at=datetime.now(UTC),
    )
    orchestrator = FakeOrchestrator()
    outcome = await SandboxAgent(
        zendesk=FakeZendesk(snapshot),
        model=ChildPlanModel(),
        tools=ToolRegistry(),
        subagent_orchestrator=orchestrator,
        skill_loader=None,
        sandbox_workdir=tmp_path,
        runtime_bootstrap=_bootstrap(),
    ).run(ticket_id=1, session_id="root-session")

    assert orchestrator.calls[0][0] == "logs"
    assert outcome.evidence_ids == ("child-ev",)
    assert outcome.todo_summary.completed == 1
    assert outcome.telemetry is not None
    assert outcome.telemetry.working_memory_fact_keys == ("subagent.child-task",)
    assert outcome.telemetry.working_memory_fact_count == 1


@pytest.mark.asyncio
async def test_root_plan_dynamically_fans_out_independent_child_todos(
    tmp_path: Path,
) -> None:
    snapshot = TicketSnapshot(
        ticket_id=1,
        status="open",
        requester_id=1,
        assignee_id=2,
        messages=(
            TicketMessage(
                event_id="evt-1",
                author_origin="customer",
                public=True,
                body="Investigate this",
                created_at=datetime.now(UTC),
            ),
        ),
        locale="en-US",
        latest_customer_cursor="evt-1",
        complete=True,
        fetched_at=datetime.now(UTC),
    )
    orchestrator = FakeOrchestrator()
    outcome = await SandboxAgent(
        zendesk=FakeZendesk(snapshot),
        model=ParallelChildPlanModel(),
        tools=ToolRegistry(),
        subagent_orchestrator=orchestrator,
        skill_loader=None,
        sandbox_workdir=tmp_path,
        runtime_bootstrap=_bootstrap(),
    ).run(ticket_id=1, session_id="root-session")

    assert len(orchestrator.batches) == 1
    assert len(orchestrator.batches[0]) == 3
    assert len(orchestrator.calls) == 3
    assert outcome.kind == "answer", outcome.reason
    assert outcome.todo_summary.completed == 3
    assert outcome.telemetry is not None
    assert len(outcome.telemetry.subagent_tasks or ()) == 3
