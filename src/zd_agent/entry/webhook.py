"""Compatibility exports for the classic webhook server."""
from __future__ import annotations

from zd_agent.services.webhook_server import (
    COMMENT_ADDED_EVENT_TYPE,
    ENTERPRISE_TAG,
    MANAGER_APP_KEY,
    SIGNATURE_HEADER,
    SIGNATURE_MAX_AGE_SECONDS,
    SIGNATURE_TIMESTAMP_HEADER,
    SUPPORTED_EVENT_TYPE,
    TRACKER_APP_KEY,
    create_app,
    handle_health,
    handle_status,
    handle_zendesk_webhook,
    run_webhook_server,
)

__all__ = [
    "COMMENT_ADDED_EVENT_TYPE",
    "ENTERPRISE_TAG",
    "MANAGER_APP_KEY",
    "SIGNATURE_HEADER",
    "SIGNATURE_MAX_AGE_SECONDS",
    "SIGNATURE_TIMESTAMP_HEADER",
    "SUPPORTED_EVENT_TYPE",
    "TRACKER_APP_KEY",
    "create_app",
    "handle_health",
    "handle_status",
    "handle_zendesk_webhook",
    "run_webhook_server",
]
