"""Ticket-id driven Agent runtime executed inside one ephemeral sandbox."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from casework_agent_core.context import ContextCheckpoint, ContextGovernance
from casework_agent_core.domain import (
    ActiveQuestion,
    TicketSnapshot,
    resolve_active_question,
)
from casework_agent_core.evidence import EvidenceGate
from casework_agent_core.skills import LoadedSkill, SkillLoader
from casework_agent_core.todo import TodoPlan
from casework_tool_sdk.loop import ToolLoop
from casework_tool_sdk.registry import ToolExecutionContext, ToolRegistry
from contracts.outcome import Outcome, OutcomeKind
from contracts.todo import Todo


class ZendeskReader(Protocol):
    async def fetch_ticket(self, ticket_id: int) -> TicketSnapshot: ...


class CaseworkModel(Protocol):
    async def plan(
        self,
        snapshot: TicketSnapshot,
        question: ActiveQuestion,
        skill: LoadedSkill,
        available_tools: tuple[str, ...],
    ) -> TodoPlan: ...

    async def compose_answer(
        self,
        snapshot: TicketSnapshot,
        question: ActiveQuestion,
        evidence_ids: tuple[str, ...],
    ) -> str: ...


@dataclass(frozen=True, slots=True)
class TodoSummary:
    completed: int
    pending: int
    failed: int


class RuntimeOutcome(Outcome):
    """Public terminal contract with compact execution summary."""

    todo_summary: TodoSummary
    checkpoint: ContextCheckpoint | None = None


class SandboxAgent:
    def __init__(
        self,
        *,
        zendesk: ZendeskReader,
        model: CaseworkModel,
        tools: ToolRegistry,
        skill_loader: SkillLoader | None = None,
        context_window_tokens: int = 100_000,
        max_tool_rounds: int = 8,
    ) -> None:
        self._zendesk = zendesk
        self._model = model
        self._tools = tools
        skills_root = Path(__file__).resolve().parents[4] / "skills"
        self._skills = skill_loader or SkillLoader(skills_root)
        self._context_window_tokens = context_window_tokens
        self._max_tool_rounds = max_tool_rounds

    async def run(self, *, ticket_id: int, session_id: str) -> RuntimeOutcome:
        context = ContextGovernance(window_tokens=self._context_window_tokens)
        try:
            snapshot = await self._zendesk.fetch_ticket(ticket_id)
            if snapshot.ticket_id != ticket_id:
                raise ValueError("Zendesk returned a different ticket snapshot")
            question = resolve_active_question(snapshot)
            context.add(
                "ticket "
                f"{snapshot.ticket_id} event {snapshot.latest_customer_cursor}",
                tokens=8,
            )
            context.add(
                question.original_request,
                tokens=max(1, len(question.original_request) // 4),
            )
            skill = self._skills.load("core-casework")
            plan = await self._model.plan(
                snapshot,
                question,
                skill,
                self._tools.names(),
            )
            gate = EvidenceGate()
            loop = ToolLoop(registry=self._tools, max_rounds=self._max_tool_rounds)
            tool_calls: list[tuple[str, dict[str, object]]] = []
            ready_tasks: list[Todo] = []
            while (task := plan.next_ready()) is not None:
                ready_tasks.append(task)
                if not task.suggested_tools:
                    break
                tool_calls.append(
                    (task.suggested_tools[0], {"query": question.original_request})
                )
                if len(tool_calls) >= self._max_tool_rounds:
                    break
            loop_result = await loop.run(
                calls=tool_calls,
                context=ToolExecutionContext(
                    ticket_id=ticket_id,
                    session_id=session_id,
                    root_session_id=session_id,
                ),
            )
            for index, result in enumerate(loop_result.results):
                gate.admit(
                    call_id=f"call-{ticket_id}-{index + 1:02d}",
                    tool_name=ready_tasks[index].suggested_tools[0],
                    result=result,
                )
                plan.mark_completed(ready_tasks[index].task_id)
                context.add(result.summary, tokens=max(1, len(result.summary) // 4))
            evidence_ids = tuple(item.evidence_id for item in gate.records())
            checkpoint = context.checkpoint(active_question=question)
            answer = await self._model.compose_answer(snapshot, question, evidence_ids)
            if not gate.can_answer(answer=answer, evidence_ids=evidence_ids):
                return self._outcome(
                    session_id=session_id,
                    ticket_id=ticket_id,
                    kind=OutcomeKind.FAILED,
                    reason="completion gate rejected an unsupported answer",
                    plan=plan,
                    checkpoint=checkpoint,
                )
            return RuntimeOutcome(
                outcome_id=f"outcome-{ticket_id}",
                session_id=session_id,
                ticket_id=ticket_id,
                kind=OutcomeKind.ANSWER,
                answer=answer,
                evidence_ids=evidence_ids,
                todo_summary=self._summary(plan),
                checkpoint=checkpoint,
            )
        except Exception as error:
            return RuntimeOutcome(
                outcome_id=f"outcome-{ticket_id}",
                session_id=session_id,
                ticket_id=ticket_id,
                kind=OutcomeKind.FAILED,
                reason=str(error),
                todo_summary=TodoSummary(completed=0, pending=0, failed=1),
            )

    @staticmethod
    def _summary(plan: TodoPlan) -> TodoSummary:
        from contracts.todo import TodoStatus

        return TodoSummary(
            completed=sum(item.status is TodoStatus.COMPLETED for item in plan.items),
            pending=sum(
                item.status
                in {TodoStatus.PENDING, TodoStatus.READY, TodoStatus.RUNNING}
                for item in plan.items
            ),
            failed=sum(
                item.status in {TodoStatus.FAILED, TodoStatus.BLOCKED}
                for item in plan.items
            ),
        )

    @classmethod
    def _outcome(
        cls,
        *,
        session_id: str,
        ticket_id: int,
        kind: OutcomeKind,
        reason: str,
        plan: TodoPlan,
        checkpoint: ContextCheckpoint,
    ) -> RuntimeOutcome:
        return RuntimeOutcome(
            outcome_id=f"outcome-{ticket_id}",
            session_id=session_id,
            ticket_id=ticket_id,
            kind=kind,
            reason=reason,
            todo_summary=cls._summary(plan),
            checkpoint=checkpoint,
        )
