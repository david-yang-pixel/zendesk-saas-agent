from __future__ import annotations

from typing import Any

from zd_agent.config import get_config
from zd_agent.integrations.tool_service_mcp import call_mcp_tool, mcp_endpoint

SERVICE = "custom-mcp"


def _custom_service_config(service_name: str, cfg: Any | None = None) -> Any | None:
    cfg = cfg or get_config()
    normalized = service_name.strip()
    for service in cfg.mcp_services.custom:
        if service.enabled and service.name == normalized:
            return service
    return None


def custom_mcp_enabled(service_name: str, cfg: Any | None = None) -> bool:
    service = _custom_service_config(service_name, cfg)
    return bool(service and mcp_endpoint(service.mcp_url))


async def call_custom_mcp_tool(
    *,
    service_name: str,
    tool_name: str,
    arguments: dict[str, Any],
    cfg: Any | None = None,
) -> dict[str, Any]:
    service = _custom_service_config(service_name, cfg)
    if service is None:
        return {
            "success": False,
            "code": "CUSTOM_MCP_SERVICE_NOT_FOUND",
            "error": f"Custom MCP service is not configured or disabled: {service_name}",
        }
    return await call_mcp_tool(
        service=f"{SERVICE}:{service.name}",
        mcp_url=service.mcp_url,
        mcp_api_key=service.mcp_api_key,
        request_timeout_seconds=service.request_timeout_seconds,
        name=tool_name,
        arguments=arguments,
        missing_code="CUSTOM_MCP_URL_MISSING",
    )
