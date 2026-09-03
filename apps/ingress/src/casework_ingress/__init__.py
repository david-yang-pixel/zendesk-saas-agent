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
from .messaging import (
    MessagingEvent,
    MessagingPayloadError,
    decode_json_body,
    parse_messaging_payload,
    verify_messaging_signature,
    verify_zendesk_signature,
)
from .ports import CancelSink, IdempotencyPort, IdempotencyStore, JobSink
from .processing_rules import (
    NormalizedZendeskEvent,
    ProcessingDecision,
    ProcessingRuleConfig,
    evaluate_processing_rules,
)
from .zendesk import (
    ZendeskApiTicketReader,
    ZendeskTicket,
    ZendeskTicketApi,
    ZendeskTicketClient,
    ZendeskTicketError,
    ZendeskTicketPort,
    ZendeskTicketReader,
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
    "MessagingEvent",
    "MessagingPayloadError",
    "NormalizedZendeskEvent",
    "ProcessingDecision",
    "ProcessingRuleConfig",
    "WebhookResponse",
    "ZendeskApiTicketReader",
    "ZendeskTicket",
    "ZendeskTicketApi",
    "ZendeskTicketClient",
    "ZendeskTicketError",
    "ZendeskTicketPort",
    "ZendeskTicketReader",
    "ZendeskWebhookPayload",
    "create_app",
    "decode_json_body",
    "evaluate_processing_rules",
    "parse_messaging_payload",
    "verify_messaging_signature",
    "verify_zendesk_signature",
]
