"""Durable harness job contract and state machine."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import Field, StrictInt, field_validator

from contracts._base import (
    ContractModel,
    require_aware,
    utc_now,
    validate_transition,
)


class JobStatus(StrEnum):
    QUEUED = "queued"
    CLAIMED = "claimed"
    SANDBOX_CREATING = "sandbox_creating"
    RUNNING = "running"
    FINALIZING = "finalizing"
    COMPLETED = "completed"
    AWAITING_CUSTOMER = "awaiting_customer"
    HUMAN_TAKEOVER = "human_takeover"
    FAILED = "failed"
    CANCELLED = "cancelled"


_JOB_TRANSITIONS: dict[JobStatus, frozenset[JobStatus]] = {
    JobStatus.QUEUED: frozenset({JobStatus.CLAIMED, JobStatus.CANCELLED}),
    JobStatus.CLAIMED: frozenset(
        {JobStatus.SANDBOX_CREATING, JobStatus.CANCELLED, JobStatus.FAILED}
    ),
    JobStatus.SANDBOX_CREATING: frozenset(
        {JobStatus.RUNNING, JobStatus.CANCELLED, JobStatus.FAILED}
    ),
    JobStatus.RUNNING: frozenset(
        {
            JobStatus.FINALIZING,
            JobStatus.AWAITING_CUSTOMER,
            JobStatus.HUMAN_TAKEOVER,
            JobStatus.FAILED,
            JobStatus.CANCELLED,
        }
    ),
    JobStatus.FINALIZING: frozenset(
        {
            JobStatus.COMPLETED,
            JobStatus.AWAITING_CUSTOMER,
            JobStatus.HUMAN_TAKEOVER,
            JobStatus.FAILED,
            JobStatus.CANCELLED,
        }
    ),
}


class CaseJob(ContractModel):
    """Durable queue row with minimal Agent business input."""

    job_id: str = Field(min_length=1)
    event_id: str = Field(min_length=1)
    ticket_id: StrictInt = Field(gt=0)
    config_revision: str = Field(min_length=1)
    model_digest: str = Field(min_length=1)
    skill_digest: str = Field(min_length=1)
    tool_digest: str = Field(min_length=1)
    status: JobStatus = JobStatus.QUEUED
    attempts: int = Field(default=0, ge=0)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime | None = None

    @field_validator("created_at", "updated_at")
    @classmethod
    def _aware_timestamps(cls, value: datetime | None) -> datetime | None:
        return None if value is None else require_aware(value, "job timestamp")

    @property
    def business_input(self) -> dict[str, int]:
        return {"ticket_id": self.ticket_id}

    def transition(self, target: JobStatus) -> CaseJob:
        target = JobStatus(target)
        validate_transition(self.status, target, _JOB_TRANSITIONS, "job")
        return self.model_copy(update={"status": target, "updated_at": utc_now()})


Job = CaseJob
ProcessingJob = CaseJob
JobState = JobStatus
