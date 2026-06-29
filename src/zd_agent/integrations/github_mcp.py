"""Remote MCP wrapper for GitHub PR and code search tools."""

from __future__ import annotations

from typing import Any

from zd_agent.config import get_config
from zd_agent.integrations.tool_service_mcp import call_mcp_tool, mcp_endpoint

SERVICE = "github-mcp"


def github_mcp_enabled(cfg: Any | None = None) -> bool:
    cfg = cfg or get_config()
    return bool(mcp_endpoint(cfg.github_mcp.mcp_url))


async def call_github_tool(
    name: str,
    arguments: dict[str, Any],
    *,
    cfg: Any | None = None,
) -> dict[str, Any]:
    cfg = cfg or get_config()
    service_cfg = cfg.github_mcp
    return await call_mcp_tool(
        service=SERVICE,
        mcp_url=service_cfg.mcp_url,
        mcp_api_key=service_cfg.mcp_api_key,
        request_timeout_seconds=service_cfg.request_timeout_seconds,
        name=name,
        arguments=arguments,
        missing_code="GITHUB_MCP_URL_MISSING",
    )
