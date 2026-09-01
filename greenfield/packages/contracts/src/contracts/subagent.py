"""Depth-one Subagent task and independent model configuration."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import (
    AliasChoices,
    Field,
    JsonValue,
    StrictInt,
    field_validator,
    model_validator,
)

from contracts._base import ContractModel, require_aware, utc_now, validate_transition


class SubagentStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"


_SUBAGENT_TRANSITIONS: dict[SubagentStatus, frozenset[SubagentStatus]] = {
    SubagentStatus.QUEUED: frozenset(
        {SubagentStatus.RUNNING, SubagentStatus.CANCELLED}
    ),
    SubagentStatus.RUNNING: frozenset(
        {
            SubagentStatus.COMPLETED,
            SubagentStatus.FAILED,
            SubagentStatus.TIMED_OUT,
            SubagentStatus.CANCELLED,
        }
    ),
}


class Subagent(ContractModel):
    subagent_id: str = Field(
        min_length=1, validation_alias=AliasChoices("subagent_id", "id")
    )
    root_session_id: str = Field(min_length=1)
    parent_session_id: str = Field(min_length=1)
    ticket_id: StrictInt = Field(gt=0)
    slot_id: str = Field(min_length=1)
    objective: str = Field(min_length=1)
    model_provider: str = Field(
        min_length=1,
        validation_alias=AliasChoices("model_provider", "provider"),
    )
    model_name: str = Field(
        min_length=1,
        validation_alias=AliasChoices("model_name", "model"),
    )
    config_revision: str | None = Field(default=None, min_length=1)
    idempotency_key: str | None = Field(default=None, min_length=1)
    status: SubagentStatus = SubagentStatus.QUEUED
    session_id: str | None = Field(default=None, min_length=1)
    result: dict[str, JsonValue] | None = None
    error_code: str | None = Field(default=None, min_length=1)
    created_at: datetime = Field(default_factory=utc_now)
    started_at: datetime | None = None
    finished_at: datetime | None = None

    @field_validator("created_at", "started_at", "finished_at")
    @classmethod
    def _aware_timestamps(cls, value: datetime | None) -> datetime | None:
        return None if value is None else require_aware(value, "subagent timestamp")

    @model_validator(mode="after")
    def _validate_depth_one(self) -> Subagent:
        if self.parent_session_id != self.root_session_id:
            raise ValueError("Subagent tasks must have a depth-one root parent")
        if self.status is SubagentStatus.COMPLETED and self.result is None:
            raise ValueError("completed Subagent requires a result")
        return self

    @property
    def provider(self) -> str:
        return self.model_provider

    @property
    def model(self) -> str:
        return self.model_name

    def transition(
        self,
        target: SubagentStatus,
        *,
        result: dict[str, JsonValue] | None = None,
        error_code: str | None = None,
    ) -> Subagent:
        target = SubagentStatus(target)
        validate_transition(self.status, target, _SUBAGENT_TRANSITIONS, "Subagent")
        if target is SubagentStatus.COMPLETED and result is None:
            raise ValueError("completed Subagent requires a result")
        return self.model_copy(
            update={"status": target, "result": result, "error_code": error_code}
        )


SubagentTask = Subagent
SubagentTaskStatus = SubagentStatus
