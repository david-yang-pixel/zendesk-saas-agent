"""PostgreSQL persistence ports for ZendeskCasework."""

from casework_persistence.db import create_engine, create_session_factory, session_scope
from casework_persistence.models import (
    ACTIVE_JOB_STATUSES,
    Base,
    CaseJobRow,
    EventRecord,
    OutboxMessage,
    TicketState,
)
from casework_persistence.repositories import (
    CaseworkPersistence,
    ConfigSnapshot,
    EnqueueResult,
    EventRepository,
    GenerationFenceError,
    HumanCancelRepository,
    JobRepository,
    MissingConfigSnapshotError,
    OutboxDraft,
    OutboxRepository,
    OutboxStatus,
    TicketRepository,
    session_transaction,
)

__all__ = [
    "ACTIVE_JOB_STATUSES",
    "Base",
    "CaseJobRow",
    "CaseworkPersistence",
    "ConfigSnapshot",
    "EnqueueResult",
    "EventRecord",
    "EventRepository",
    "GenerationFenceError",
    "HumanCancelRepository",
    "JobRepository",
    "MissingConfigSnapshotError",
    "OutboxDraft",
    "OutboxMessage",
    "OutboxRepository",
    "OutboxStatus",
    "TicketRepository",
    "TicketState",
    "create_engine",
    "create_session_factory",
    "session_scope",
    "session_transaction",
]
