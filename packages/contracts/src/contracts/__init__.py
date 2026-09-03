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
from contracts.runtime import ChildModelBinding, ModelBinding, RuntimeBootstrap
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
from contracts.telemetry import (
    TELEMETRY_VERSION,
    ActiveQuestionTelemetry,
    ArtifactReference,
    ContextCheckpointTelemetry,
    SandboxLifecycleEvent,
    SandboxResultTelemetry,
    SubagentTaskTelemetry,
    TodoTelemetry,
    ToolCallTelemetry,
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
    "TELEMETRY_VERSION",
    "ActiveQuestion",
    "ActiveQuestionTelemetry",
    "AgentSession",
    "ArtifactReference",
    "CaseJob",
    "CaseworkOutcome",
    "ChildModelBinding",
    "ContextCheckpointTelemetry",
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
    "ModelBinding",
    "NormalizedWebhookEvent",
    "Outcome",
    "OutcomeDisposition",
    "OutcomeKind",
    "ProcessingJob",
    "RunOutcome",
    "RuntimeBootstrap",
    "Sandbox",
    "SandboxCancel",
    "SandboxCancelRequest",
    "SandboxHeartbeat",
    "SandboxLifecycleEvent",
    "SandboxResources",
    "SandboxResult",
    "SandboxResultMessage",
    "SandboxResultTelemetry",
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
    "SubagentTaskTelemetry",
    "TicketAttachment",
    "TicketEvent",
    "TicketMessage",
    "TicketSnapshot",
    "Todo",
    "TodoItem",
    "TodoState",
    "TodoStatus",
    "TodoTelemetry",
    "ToolCall",
    "ToolCallStatus",
    "ToolCallTelemetry",
    "ToolDefinition",
    "ToolResult",
    "ToolResultStatus",
    "ToolStatus",
    "WebhookEvent",
    "ZendeskEvent",
]
