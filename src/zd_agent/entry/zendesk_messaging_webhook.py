"""Compatibility wrapper for the Zendesk Messaging webhook service."""
from zd_agent.services.zendesk_messaging_webhook import (
    handle_zendesk_messaging_webhook,
    verify_messaging_signature,
)

__all__ = ["handle_zendesk_messaging_webhook", "verify_messaging_signature"]
