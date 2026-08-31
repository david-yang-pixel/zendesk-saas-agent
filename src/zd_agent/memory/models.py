from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel


class MemoryKind(str, Enum):
    EPISODIC = "episodic"
    SEMANTIC = "semantic"


class MemoryStatus(str, Enum):
    PROPOSED = "proposed"
    ACTIVE = "active"
    REJECTED = "rejected"


class MemoryEntry(BaseModel):
    id: str
    kind: MemoryKind
    status: MemoryStatus
    title: str
    content: str
    tags: tuple[str, ...] = ()
    source_ticket_id: int | None = None
    evidence_references: tuple[str, ...] = ()
    proposed_by: str
    reviewed_by: str = ""
    created_at: datetime
    updated_at: datetime


class MemorySearchHit(BaseModel):
    entry: MemoryEntry
    authority: str = "historical_guidance"
    disclaimer: str = (
        "Historical navigation only; this is not current-ticket evidence and "
        "must be re-verified with current authoritative tools."
    )


class WorkingMemoryItem(BaseModel):
    root_session_id: str
    key: str
    value: Any
    writer_session_id: str
    updated_at: datetime


class MemoryFeedback(BaseModel):
    id: str
    entry_id: str
    useful: bool
    note: str
    actor: str
    created_at: datetime
