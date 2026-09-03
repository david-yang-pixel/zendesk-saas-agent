"""Tool manifest, call, and result contracts."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import Field, JsonValue, field_validator, model_validator

from contracts._base import (
    ContractModel,
    json_object,
    require_aware,
    validate_transition,
)


class ToolResultStatus(StrEnum):
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILURE = "failure"


class ToolResult(ContractModel):
    status: ToolResultStatus
    summary: str = Field(min_length=1)
    data: JsonValue | None = None
    evidence_id: str | None = Field(default=None, min_length=1)
    observed_at: datetime
    targets: tuple[str, ...] = ()
    artifacts: tuple[str, ...] = ()
    truncated: bool = False
    retryable: bool = False

    @field_validator("observed_at")
    @classmethod
    def _aware_observed_at(cls, value: datetime) -> datetime:
        return require_aware(value, "tool result observed_at")

    @model_validator(mode="after")
    def _evidence_for_success(self) -> ToolResult:
        if self.status is ToolResultStatus.SUCCESS and not self.evidence_id:
            raise ValueError("successful tool result requires an evidence_id")
        return self


class ToolDefinition(ContractModel):
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    input_schema: dict[str, JsonValue]
    read_only: bool = True
    root_only: bool = False


class ToolCallStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


_TOOL_CALL_TRANSITIONS: dict[ToolCallStatus, frozenset[ToolCallStatus]] = {
    ToolCallStatus.QUEUED: frozenset(
        {ToolCallStatus.RUNNING, ToolCallStatus.CANCELLED}
    ),
    ToolCallStatus.RUNNING: frozenset(
        {ToolCallStatus.SUCCEEDED, ToolCallStatus.FAILED, ToolCallStatus.CANCELLED}
    ),
}


class ToolCall(ContractModel):
    call_id: str = Field(min_length=1)
    tool_name: str = Field(min_length=1)
    arguments: dict[str, JsonValue] = Field(default_factory=dict)
    status: ToolCallStatus = ToolCallStatus.QUEUED
    result: ToolResult | None = None

    @field_validator("arguments")
    @classmethod
    def _object_arguments(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        return json_object(value)

    @model_validator(mode="after")
    def _result_matches_status(self) -> ToolCall:
        if self.status is ToolCallStatus.SUCCEEDED and self.result is None:
            raise ValueError("succeeded tool call requires a result")
        if self.status is ToolCallStatus.FAILED and self.result is None:
            raise ValueError("failed tool call requires a result")
        return self

    def transition(
        self, target: ToolCallStatus, *, result: ToolResult | None = None
    ) -> ToolCall:
        target = ToolCallStatus(target)
        validate_transition(self.status, target, _TOOL_CALL_TRANSITIONS, "tool call")
        if (
            target in {ToolCallStatus.SUCCEEDED, ToolCallStatus.FAILED}
            and result is None
        ):
            raise ValueError(f"{target.value} tool call requires a result")
        return self.model_copy(update={"status": target, "result": result})


ToolExecutionResult = ToolResult
ToolStatus = ToolResultStatus
