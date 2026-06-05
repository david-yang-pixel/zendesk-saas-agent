"""Compatibility wrapper for the Zendesk classic webhook service."""
from zd_agent.services.zendesk_webhook import (
    handle_zendesk_webhook,
    verify_zendesk_signature,
)

__all__ = ["handle_zendesk_webhook", "verify_zendesk_signature"]
