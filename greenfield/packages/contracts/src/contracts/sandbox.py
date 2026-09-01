"""Ephemeral per-run sandbox contract and lifecycle."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import AliasChoices, Field, StrictInt, field_validator, model_validator

from contracts._base import ContractModel, require_aware, validate_transition
from contracts.outcome import Outcome


class SandboxStatus(StrEnum):
    CREATING = "creating"
    READY = "ready"
    RUNNING = "running"
    DESTROYING = "destroying"
    DESTROYED = "destroyed"


class SandboxResources(ContractModel):
    """Fixed resource policy: default, then at most one OOM retry."""

    cpu: float = Field(default=0.5, gt=0, le=1.0)
    memory_mb: StrictInt = Field(default=1024, gt=0)
    timeout_seconds: StrictInt = Field(default=1800, gt=0)

    @property
    def memory_mib(self) -> int:
        return self.memory_mb

    @model_validator(mode="after")
    def _validate_policy(self) -> SandboxResources:
        if self.memory_mb != 1024:
            raise ValueError("sandbox memory must be 1024 MiB")
        if self.timeout_seconds != 1800:
            raise ValueError("sandbox timeout must be 1800 seconds")
        if self.cpu not in {0.5, 1.0}:
            raise ValueError("sandbox CPU must be 0.5 or 1.0")
        return self


_SANDBOX_TRANSITIONS: dict[SandboxStatus, frozenset[SandboxStatus]] = {
    SandboxStatus.CREATING: frozenset(
        {SandboxStatus.READY, SandboxStatus.DESTROYING}
    ),
    SandboxStatus.READY: frozenset({SandboxStatus.RUNNING, SandboxStatus.DESTROYING}),
    SandboxStatus.RUNNING: frozenset({SandboxStatus.DESTROYING}),
    SandboxStatus.DESTROYING: frozenset({SandboxStatus.DESTROYED}),
}


class Sandbox(ContractModel):
    sandbox_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    status: SandboxStatus = SandboxStatus.CREATING
    resources: SandboxResources = Field(default_factory=SandboxResources)
    resource_attempt: StrictInt = Field(
        default=1,
        ge=1,
        le=2,
        validation_alias=AliasChoices("resource_attempt", "attempt"),
    )

    @model_validator(mode="after")
    def _validate_attempt_resources(self) -> Sandbox:
        expected_cpu = 1.0 if self.resource_attempt == 2 else 0.5
        if self.resources.cpu != expected_cpu:
            raise ValueError(
                "resource attempt 1 requires 0.5 CPU and attempt 2 requires 1 CPU"
            )
        return self

    def transition(self, target: SandboxStatus) -> Sandbox:
        target = SandboxStatus(target)
        validate_transition(self.status, target, _SANDBOX_TRANSITIONS, "sandbox")
        return self.model_copy(update={"status": target})


SandboxSpec = SandboxResources
SandboxState = SandboxStatus


class SandboxStartRequest(ContractModel):
    """Harness-to-sandbox start message with an immutable runtime snapshot."""

    sandbox_id: str = Field(min_length=1)
    job_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    ticket_id: StrictInt = Field(gt=0)
    generation: StrictInt = Field(ge=1)
    resource_attempt: StrictInt = Field(ge=1, le=2)
    deadline: datetime
    resources: SandboxResources = Field(default_factory=SandboxResources)
    config_revision: str = Field(min_length=1)
    model_digest: str = Field(min_length=1)
    skill_digest: str = Field(min_length=1)
    tool_digest: str = Field(min_length=1)

    @field_validator("deadline")
    @classmethod
    def _aware_deadline(cls, value: datetime) -> datetime:
        return require_aware(value, "sandbox start deadline")

    @model_validator(mode="after")
    def _attempt_matches_resources(self) -> SandboxStartRequest:
        expected_cpu = 1.0 if self.resource_attempt == 2 else 0.5
        if self.resources.cpu != expected_cpu:
            raise ValueError("sandbox start attempt does not match resources")
        return self


class SandboxCancelRequest(ContractModel):
    """Harness-to-sandbox cancellation message."""

    sandbox_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    generation: StrictInt = Field(ge=1)
    reason: str = Field(min_length=1)
    requested_at: datetime | None = None

    @field_validator("requested_at")
    @classmethod
    def _aware_requested_at(cls, value: datetime | None) -> datetime | None:
        return (
            None
            if value is None
            else require_aware(value, "sandbox cancel requested_at")
        )


class SandboxHeartbeat(ContractModel):
    sandbox_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    observed_at: datetime

    @field_validator("observed_at")
    @classmethod
    def _aware_observed_at(cls, value: datetime) -> datetime:
        return require_aware(value, "sandbox heartbeat observed_at")


class SandboxResultMessage(ContractModel):
    """Sandbox-to-harness result message; outcome remains a typed contract."""

    sandbox_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    outcome: Outcome
    finished_at: datetime | None = None
    artifacts: tuple[str, ...] = ()

    @field_validator("finished_at")
    @classmethod
    def _aware_finished_at(cls, value: datetime | None) -> datetime | None:
        return (
            None
            if value is None
            else require_aware(value, "sandbox result finished_at")
        )


SandboxStart = SandboxStartRequest
SandboxCancel = SandboxCancelRequest
SandboxResult = SandboxResultMessage
