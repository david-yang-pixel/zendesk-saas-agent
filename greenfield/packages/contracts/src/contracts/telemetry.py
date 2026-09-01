"""Versioned, bounded telemetry returned with one sandbox outcome.

Telemetry is an operator-facing projection, not a transcript.  It may contain
small summaries and references to separately governed artifacts, but it never
contains tool arguments, tool payloads, prompts, or hidden model reasoning.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator, model_validator

from contracts._base import ContractModel, require_aware, unique_strings

TELEMETRY_VERSION = "sandbox-result-telemetry.v1"


def _unique(values: tuple[str, ...], field_name: str) -> tuple[str, ...]:
    unique_strings(values, field_name)
    return values


class ArtifactReference(ContractModel):
    """A bounded pointer to an externally governed artifact."""

    ref: str = Field(min_length=1, max_length=512)
    summary: str | None = Field(default=None, max_length=2048)

    @field_validator("ref")
    @classmethod
    def _controlled_reference(cls, value: str) -> str:
        if any(character.isspace() for character in value):
            raise ValueError("artifact reference must not contain whitespace")
        if "://" not in value:
            raise ValueError("artifact reference must be a URI")
        return value


class ActiveQuestionTelemetry(ContractModel):
    original_event_id: str = Field(min_length=1, max_length=255)
    original_request: str = Field(min_length=1, max_length=4096)
    clarification_event_ids: tuple[str, ...] = ()
    current_facts: tuple[str, ...] = ()

    @field_validator("clarification_event_ids", "current_facts")
    @classmethod
    def _bounded_identifiers(
        cls, value: tuple[str, ...]
    ) -> tuple[str, ...]:
        return _unique(value, "question telemetry identifiers")


class TodoTelemetry(ContractModel):
    task_id: str = Field(min_length=1, max_length=255)
    goal: str = Field(min_length=1, max_length=2048)
    status: Literal[
        "pending", "ready", "running", "completed", "failed", "blocked", "cancelled"
    ]
    assigned_to: str | None = Field(default=None, max_length=255)


class ToolCallTelemetry(ContractModel):
    call_id: str = Field(min_length=1, max_length=255)
    tool_name: str = Field(min_length=1, max_length=255)
    status: Literal["queued", "running", "succeeded", "failed", "cancelled"]
    summary: str = Field(min_length=1, max_length=2048)
    evidence_ids: tuple[str, ...] = ()
    artifact_refs: tuple[ArtifactReference, ...] = ()

    @field_validator("evidence_ids")
    @classmethod
    def _unique_evidence(
        cls, value: tuple[str, ...]
    ) -> tuple[str, ...]:
        return _unique(value, "tool telemetry evidence_ids")


class SubagentTaskTelemetry(ContractModel):
    subagent_id: str = Field(min_length=1, max_length=255)
    objective: str = Field(min_length=1, max_length=2048)
    status: Literal[
        "queued", "running", "completed", "failed", "timed_out", "cancelled"
    ]
    slot_id: str | None = Field(default=None, max_length=255)
    session_id: str | None = Field(default=None, max_length=255)
    evidence_ids: tuple[str, ...] = ()

    @field_validator("evidence_ids")
    @classmethod
    def _unique_subagent_evidence(
        cls, value: tuple[str, ...]
    ) -> tuple[str, ...]:
        return _unique(value, "subagent telemetry evidence_ids")


class ContextCheckpointTelemetry(ContractModel):
    checkpoint_ref: str = Field(min_length=1, max_length=512)
    level: Literal["none", "soft", "hard"]
    used_tokens: int = Field(ge=0)
    exact_identifiers: tuple[str, ...] = ()
    retained_rounds: tuple[str, ...] = ()
    summary: str | None = Field(default=None, max_length=4096)

    @field_validator("exact_identifiers")
    @classmethod
    def _unique_checkpoint_identifiers(
        cls, value: tuple[str, ...]
    ) -> tuple[str, ...]:
        return _unique(value, "checkpoint exact_identifiers")


class SandboxLifecycleEvent(ContractModel):
    state: Literal["creating", "ready", "running", "destroying", "destroyed"]
    occurred_at: datetime
    sandbox_id: str = Field(min_length=1, max_length=255)
    attempt: int = Field(ge=1, le=2)
    detail: str | None = Field(default=None, max_length=512)

    @field_validator("occurred_at")
    @classmethod
    def _aware_timestamp(cls, value: datetime) -> datetime:
        return require_aware(value, "sandbox lifecycle occurred_at")


class SandboxResultTelemetry(ContractModel):
    """Bounded run projection persisted by Harness after sandbox finalization."""

    version: Literal["sandbox-result-telemetry.v1"] = TELEMETRY_VERSION
    active_question: ActiveQuestionTelemetry | None = None
    todos: tuple[TodoTelemetry, ...] = ()
    tool_calls: tuple[ToolCallTelemetry, ...] = ()
    subagent_tasks: tuple[SubagentTaskTelemetry, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    artifact_refs: tuple[ArtifactReference, ...] = ()
    context_checkpoint: ContextCheckpointTelemetry | None = None
    sandbox_lifecycle: tuple[SandboxLifecycleEvent, ...] = ()

    @field_validator("evidence_ids")
    @classmethod
    def _unique_run_evidence(
        cls, value: tuple[str, ...]
    ) -> tuple[str, ...]:
        return _unique(value, "run telemetry evidence_ids")

    @model_validator(mode="after")
    def _bounded_cardinality(self) -> SandboxResultTelemetry:
        for name, values, limit in (
            ("todos", self.todos, 128),
            ("tool_calls", self.tool_calls, 256),
            ("subagent_tasks", self.subagent_tasks, 64),
            ("evidence_ids", self.evidence_ids, 512),
            ("artifact_refs", self.artifact_refs, 128),
            ("sandbox_lifecycle", self.sandbox_lifecycle, 16),
        ):
            if len(values) > limit:
                raise ValueError(f"{name} exceeds telemetry limit")
        return self


__all__ = [
    "TELEMETRY_VERSION",
    "ActiveQuestionTelemetry",
    "ArtifactReference",
    "ContextCheckpointTelemetry",
    "SandboxLifecycleEvent",
    "SandboxResultTelemetry",
    "SubagentTaskTelemetry",
    "TodoTelemetry",
    "ToolCallTelemetry",
]
