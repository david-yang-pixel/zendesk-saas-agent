"""Dynamic Todo planning and deterministic readiness transitions."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from casework_agent_core.domain import ActiveQuestion, TicketSnapshot
from contracts.todo import Todo, TodoStatus


@dataclass(slots=True)
class TodoPlan:
    items: list[Todo]

    def next_ready(self) -> Todo | None:
        completed = {
            item.task_id for item in self.items if item.status is TodoStatus.COMPLETED
        }
        for index, item in enumerate(self.items):
            if item.is_ready(completed):
                ready = item.transition(TodoStatus.READY, completed_task_ids=completed)
                self.items[index] = ready
                return ready
        return None

    def take_ready_for(self, *, assigned_to: str, limit: int) -> tuple[Todo, ...]:
        """Transition a bounded wave of independent work for one assignee."""

        if limit < 1:
            return ()
        completed = {
            item.task_id for item in self.items if item.status is TodoStatus.COMPLETED
        }
        selected: list[Todo] = []
        for index, item in enumerate(self.items):
            if item.assigned_to != assigned_to or not item.is_ready(completed):
                continue
            ready = item.transition(TodoStatus.READY, completed_task_ids=completed)
            self.items[index] = ready
            selected.append(ready)
            if len(selected) >= limit:
                break
        return tuple(selected)

    def mark_completed(self, task_id: str) -> None:
        for index, item in enumerate(self.items):
            if item.task_id == task_id:
                if item.status is TodoStatus.PENDING:
                    item = item.transition(
                        TodoStatus.READY,
                        completed_task_ids={
                            other.task_id
                            for other in self.items
                            if other.status is TodoStatus.COMPLETED
                        },
                    )
                if item.status is TodoStatus.READY:
                    item = item.transition(TodoStatus.RUNNING)
                self.items[index] = item.transition(TodoStatus.COMPLETED)
                return
        raise KeyError(task_id)

    def mark_failed(self, task_id: str) -> None:
        for index, item in enumerate(self.items):
            if item.task_id == task_id:
                if item.status is TodoStatus.PENDING:
                    item = item.transition(
                        TodoStatus.READY,
                        completed_task_ids={
                            other.task_id
                            for other in self.items
                            if other.status is TodoStatus.COMPLETED
                        },
                    )
                if item.status is TodoStatus.READY:
                    item = item.transition(TodoStatus.RUNNING)
                self.items[index] = item.transition(TodoStatus.FAILED)
                return
        raise KeyError(task_id)

    @property
    def completed_count(self) -> int:
        return sum(item.status is TodoStatus.COMPLETED for item in self.items)

    @property
    def executable_count(self) -> int:
        completed = {
            item.task_id for item in self.items if item.status is TodoStatus.COMPLETED
        }
        return sum(item.is_ready(completed) for item in self.items)


@dataclass(frozen=True, slots=True)
class TodoPlanner:
    """Small deterministic fallback planner used before model planning is added."""

    def build(
        self,
        snapshot: TicketSnapshot,
        question: ActiveQuestion,
        *,
        available_tools: Sequence[str],
    ) -> TodoPlan:
        first_tool = available_tools[0] if available_tools else None
        return TodoPlan(
            items=[
                Todo(
                    task_id=f"task-{snapshot.ticket_id}-01",
                    goal="Investigate the customer's request",
                    assigned_to="root",
                    skill="core-casework",
                    suggested_tools=(first_tool,) if first_tool else (),
                    required_evidence=("customer_request",),
                    completion_condition=(
                        "One evidence-backed answer is available"
                    ),
                )
            ]
        )
