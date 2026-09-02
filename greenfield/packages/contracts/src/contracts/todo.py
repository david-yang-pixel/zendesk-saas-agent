"""Dynamic Todo plan contracts and dependency readiness."""

from __future__ import annotations

from collections.abc import Collection
from enum import StrEnum

from pydantic import Field, JsonValue, field_validator, model_validator

from contracts._base import ContractModel, unique_strings, validate_transition


class TodoStatus(StrEnum):
    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


_TODO_TRANSITIONS: dict[TodoStatus, frozenset[TodoStatus]] = {
    TodoStatus.PENDING: frozenset({TodoStatus.READY, TodoStatus.CANCELLED}),
    TodoStatus.READY: frozenset({TodoStatus.RUNNING, TodoStatus.CANCELLED}),
    TodoStatus.RUNNING: frozenset(
        {
            TodoStatus.COMPLETED,
            TodoStatus.FAILED,
            TodoStatus.BLOCKED,
            TodoStatus.CANCELLED,
        }
    ),
}


class Todo(ContractModel):
    task_id: str = Field(min_length=1)
    goal: str = Field(min_length=1)
    status: TodoStatus = TodoStatus.PENDING
    assigned_to: str = Field(default="root", min_length=1)
    dependencies: tuple[str, ...] = ()
    skill: str | None = Field(default=None, min_length=1)
    suggested_tools: tuple[str, ...] = ()
    tool_arguments: dict[str, JsonValue] = Field(default_factory=dict)
    required_evidence: tuple[str, ...] = ()
    attempts: int = Field(default=0, ge=0)
    completion_condition: str = Field(min_length=1)

    @field_validator("dependencies", "suggested_tools", "required_evidence")
    @classmethod
    def _unique_values(cls, value: tuple[str, ...], info: object) -> tuple[str, ...]:
        # ``info`` is intentionally not inspected; the generic field name is
        # supplied by Pydantic in the error context and this keeps the validator
        # reusable across the three identifier lists.
        unique_strings(value, "Todo identifier lists")
        return value

    @model_validator(mode="after")
    def _validate_dependencies(self) -> Todo:
        if self.task_id in self.dependencies:
            raise ValueError("Todo dependencies cannot include itself")
        return self

    def is_ready(self, completed_task_ids: Collection[str]) -> bool:
        return self.status is TodoStatus.PENDING and all(
            dependency in completed_task_ids for dependency in self.dependencies
        )

    def transition(
        self,
        target: TodoStatus,
        *,
        completed_task_ids: Collection[str] | None = None,
    ) -> Todo:
        target = TodoStatus(target)
        validate_transition(self.status, target, _TODO_TRANSITIONS, "Todo")
        if target is TodoStatus.READY and not self.is_ready(
            completed_task_ids or set()
        ):
            raise ValueError("Todo dependencies are not complete")
        return self.model_copy(update={"status": target})


TodoItem = Todo
TodoState = TodoStatus
