"""Remote notification-service MCP wrapper."""

from __future__ import annotations

from typing import Any

from zd_agent.config import get_config
from zd_agent.integrations.tool_service_mcp import call_mcp_tool, mcp_endpoint

SERVICE = "notification-service"

_DEFINITELY_UNSENT_CODES = frozenset(
    {
        "NOTIFICATION_SERVICE_URL_MISSING",
        "NOTIFICATION_SERVICE_UNAUTHORIZED",
        "VALIDATION_ERROR",
        "INVALID_ARGUMENT",
        "BAD_REQUEST",
        "UNAUTHORIZED",
        "FORBIDDEN",
        "NOT_FOUND",
        "DRY_RUN",
    }
)


def notification_service_enabled(cfg: Any | None = None) -> bool:
    cfg = cfg or get_config()
    return bool(mcp_endpoint(cfg.notification_service.mcp_url))


async def push_notification_card(
    *,
    card_type: str,
    ticket_id: str,
    content: str,
    title: str = "Alert",
    zendesk_url: str = "",
    urgent: bool = False,
    idempotency_reference: str = "",
    cfg: Any | None = None,
) -> dict[str, Any]:
    """Push a card through notification-service MCP only."""
    cfg = cfg or get_config()
    arguments = {
        "card_type": card_type,
        "ticket_id": ticket_id,
        "content": content,
        "title": title,
        "zendesk_url": zendesk_url,
        "urgent": urgent,
    }
    if idempotency_reference:
        arguments["idempotency_reference"] = idempotency_reference

    service_cfg = cfg.notification_service
    return await call_mcp_tool(
        service=SERVICE,
        mcp_url=service_cfg.mcp_url,
        mcp_api_key=service_cfg.mcp_api_key,
        request_timeout_seconds=service_cfg.request_timeout_seconds,
        name="feishu_push",
        arguments=arguments,
        missing_code="NOTIFICATION_SERVICE_URL_MISSING",
    )


def notification_result_is_definitely_unsent(payload: dict[str, Any]) -> bool:
    """Return true only when the notification request could not have executed."""
    code = str(payload.get("code") or "").upper()
    return code in _DEFINITELY_UNSENT_CODES
