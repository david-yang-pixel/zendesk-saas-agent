"""Deterministic Zendesk webhook ingress rules."""

from .app import WEBHOOK_PATH, WebhookResponse, ZendeskWebhookPayload, create_app
from .fakes import (
    Cancellation,
    InMemoryCancelSink,
    InMemoryIdempotency,
    InMemoryIdempotencyStore,
    InMemoryJobSink,
    JobSubmission,
)
from .ports import CancelSink, IdempotencyPort, IdempotencyStore, JobSink
from .processing_rules import (
    NormalizedZendeskEvent,
    ProcessingDecision,
    ProcessingRuleConfig,
    evaluate_processing_rules,
)

__all__ = [
    "WEBHOOK_PATH",
    "CancelSink",
    "Cancellation",
    "IdempotencyPort",
    "IdempotencyStore",
    "InMemoryCancelSink",
    "InMemoryIdempotency",
    "InMemoryIdempotencyStore",
    "InMemoryJobSink",
    "JobSink",
    "JobSubmission",
    "NormalizedZendeskEvent",
    "ProcessingDecision",
    "ProcessingRuleConfig",
    "WebhookResponse",
    "ZendeskWebhookPayload",
    "create_app",
    "evaluate_processing_rules",
]
