"""Immutable Pydantic contracts shared by the Greenfield services.

The package deliberately contains domain data only.  State changes are
represented by a validated copy returned from ``transition``; contracts never
perform I/O or call an adapter.
"""

from contracts._base import InvalidTransitionError
from contracts.event import (
    Event,
    EventType,
    HumanCancel,
    HumanCancelCommand,
    HumanCancelEvent,
    NormalizedWebhookEvent,
    TicketEvent,
    WebhookEvent,
    ZendeskEvent,
)
from contracts.evidence import (
    Evidence,
    EvidenceItem,
    EvidenceResultStatus,
    EvidenceStatus,
)
from contracts.job import CaseJob, Job, JobState, JobStatus, ProcessingJob
from contracts.outcome import (
    CaseworkOutcome,
    Outcome,
    OutcomeDisposition,
    OutcomeKind,
    RunOutcome,
)
from contracts.sandbox import (
    Sandbox,
    SandboxCancel,
    SandboxCancelRequest,
    SandboxHeartbeat,
    SandboxResources,
    SandboxResult,
    SandboxResultMessage,
    SandboxSpec,
    SandboxStart,
    SandboxStartRequest,
    SandboxState,
    SandboxStatus,
)
from contracts.session import (
    AgentSession,
    Session,
    SessionKind,
    SessionState,
    SessionStatus,
)
from contracts.subagent import (
    Subagent,
    SubagentStatus,
    SubagentTask,
    SubagentTaskStatus,
)
from contracts.ticket import (
    ActiveQuestion,
    TicketAttachment,
    TicketMessage,
    TicketSnapshot,
)
from contracts.todo import Todo, TodoItem, TodoState, TodoStatus
from contracts.tool import (
    ToolCall,
    ToolCallStatus,
    ToolDefinition,
    ToolResult,
    ToolResultStatus,
    ToolStatus,
)

__all__ = [
    "ActiveQuestion",
    "AgentSession",
    "CaseJob",
    "CaseworkOutcome",
    "Event",
    "EventType",
    "Evidence",
    "EvidenceItem",
    "EvidenceResultStatus",
    "EvidenceStatus",
    "HumanCancel",
    "HumanCancelCommand",
    "HumanCancelEvent",
    "InvalidTransitionError",
    "Job",
    "JobState",
    "JobStatus",
    "NormalizedWebhookEvent",
    "Outcome",
    "OutcomeDisposition",
    "OutcomeKind",
    "ProcessingJob",
    "RunOutcome",
    "Sandbox",
    "SandboxCancel",
    "SandboxCancelRequest",
    "SandboxHeartbeat",
    "SandboxResources",
    "SandboxResult",
    "SandboxResultMessage",
    "SandboxSpec",
    "SandboxStart",
    "SandboxStartRequest",
    "SandboxState",
    "SandboxStatus",
    "Session",
    "SessionKind",
    "SessionState",
    "SessionStatus",
    "Subagent",
    "SubagentStatus",
    "SubagentTask",
    "SubagentTaskStatus",
    "TicketAttachment",
    "TicketEvent",
    "TicketMessage",
    "TicketSnapshot",
    "Todo",
    "TodoItem",
    "TodoState",
    "TodoStatus",
    "ToolCall",
    "ToolCallStatus",
    "ToolDefinition",
    "ToolResult",
    "ToolResultStatus",
    "ToolStatus",
    "WebhookEvent",
    "ZendeskEvent",
]
