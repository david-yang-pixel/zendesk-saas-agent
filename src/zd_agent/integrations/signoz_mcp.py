"""Remote MCP client wrapper for SigNoz structured and sz tools."""

from __future__ import annotations

from typing import Any

from zd_agent.config import get_config
from zd_agent.integrations.tool_service_mcp import call_mcp_tool, mcp_endpoint

SERVICE = "signoz-mcp"


def signoz_mcp_enabled(cfg: Any | None = None) -> bool:
    cfg = cfg or get_config()
    return bool(mcp_endpoint(cfg.signoz.mcp_url))


async def call_signoz_tool(
    name: str,
    arguments: dict[str, Any],
    *,
    cfg: Any | None = None,
) -> dict[str, Any]:
    """Call one remote SigNoz MCP tool and preserve structured failures."""
    cfg = cfg or get_config()
    service_cfg = cfg.signoz
    return await call_mcp_tool(
        service=SERVICE,
        mcp_url=service_cfg.mcp_url,
        mcp_api_key=service_cfg.mcp_api_key,
        request_timeout_seconds=service_cfg.request_timeout_seconds,
        name=name,
        arguments=arguments,
        missing_code="SIGNOZ_MCP_URL_MISSING",
    )


async def run_signoz_sz(
    *args: str,
    cfg: Any | None = None,
) -> list[Any] | dict[str, Any] | None:
    """Compatibility wrapper for legacy sz-based tools."""
    payload = await call_signoz_tool("sz_run", {"args": list(args)}, cfg=cfg)
    if not payload.get("success"):
        return None
    data = payload.get("data")
    return data if isinstance(data, (list, dict)) else None
