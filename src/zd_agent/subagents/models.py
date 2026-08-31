from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class SubagentTaskStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


TERMINAL_SUBAGENT_STATUSES = frozenset(
    {
        SubagentTaskStatus.COMPLETED,
        SubagentTaskStatus.FAILED,
        SubagentTaskStatus.CANCELLED,
        SubagentTaskStatus.TIMED_OUT,
    }
)


class SubagentTask(BaseModel):
    id: str
    root_session_id: str
    parent_session_id: str
    ticket_id: int
    slot_id: str
    objective: str
    idempotency_key: str
    config_digest: str
    model_snapshot: dict[str, Any] = Field(default_factory=dict)
    status: SubagentTaskStatus
    session_id: str = ""
    result: dict[str, Any] = Field(default_factory=dict)
    error_code: str = ""
    error_detail: str = ""
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
