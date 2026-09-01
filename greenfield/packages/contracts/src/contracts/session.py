"""Root and child Agent session contract."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import AliasChoices, Field, StrictInt, field_validator, model_validator

from contracts._base import (
    ContractModel,
    require_aware,
    utc_now,
    validate_transition,
)


class SessionKind(StrEnum):
    ROOT = "root"
    SUBAGENT = "subagent"


class SessionStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    FINALIZING = "finalizing"
    COMPLETED = "completed"
    AWAITING_CUSTOMER = "awaiting_customer"
    HUMAN_TAKEOVER = "human_takeover"
    FAILED = "failed"
    CANCELLED = "cancelled"


_SESSION_TRANSITIONS: dict[SessionStatus, frozenset[SessionStatus]] = {
    SessionStatus.QUEUED: frozenset({SessionStatus.RUNNING, SessionStatus.CANCELLED}),
    SessionStatus.RUNNING: frozenset(
        {
            SessionStatus.FINALIZING,
            SessionStatus.COMPLETED,
            SessionStatus.AWAITING_CUSTOMER,
            SessionStatus.HUMAN_TAKEOVER,
            SessionStatus.FAILED,
            SessionStatus.CANCELLED,
        }
    ),
    SessionStatus.FINALIZING: frozenset(
        {
            SessionStatus.COMPLETED,
            SessionStatus.AWAITING_CUSTOMER,
            SessionStatus.HUMAN_TAKEOVER,
            SessionStatus.FAILED,
            SessionStatus.CANCELLED,
        }
    ),
}


class Session(ContractModel):
    session_id: str = Field(min_length=1)
    ticket_id: StrictInt = Field(gt=0)
    job_id: str | None = Field(default=None, min_length=1)
    status: SessionStatus = SessionStatus.QUEUED
    kind: SessionKind = Field(
        default=SessionKind.ROOT,
        validation_alias=AliasChoices("kind", "session_kind"),
    )
    root_session_id: str | None = Field(default=None, min_length=1)
    parent_session_id: str | None = Field(default=None, min_length=1)
    config_revision: str = Field(min_length=1)
    model_digest: str = Field(min_length=1)
    skill_digest: str = Field(min_length=1)
    tool_digest: str = Field(min_length=1)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime | None = None

    @field_validator("created_at", "updated_at")
    @classmethod
    def _aware_timestamps(cls, value: datetime | None) -> datetime | None:
        return None if value is None else require_aware(value, "session timestamp")

    @model_validator(mode="after")
    def _validate_parentage(self) -> Session:
        if self.kind is SessionKind.ROOT:
            if self.parent_session_id is not None:
                raise ValueError("root session cannot have a parent session")
        else:
            if self.root_session_id is None or self.parent_session_id is None:
                raise ValueError("subagent session requires root and parent sessions")
            if self.parent_session_id != self.root_session_id:
                raise ValueError("subagent sessions must be depth one")
        return self

    def transition(self, target: SessionStatus) -> Session:
        target = SessionStatus(target)
        validate_transition(self.status, target, _SESSION_TRANSITIONS, "session")
        return self.model_copy(update={"status": target, "updated_at": utc_now()})


AgentSession = Session
SessionState = SessionStatus
