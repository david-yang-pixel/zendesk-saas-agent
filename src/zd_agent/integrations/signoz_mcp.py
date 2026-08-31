"""Remote MCP client wrapper for SigNoz structured and sz tools."""

from __future__ import annotations

from typing import Any

from zd_agent.config import get_config
from zd_agent.integrations.tool_service_mcp import call_mcp_tool, mcp_endpoint

SERVICE = "signoz-mcp"


class SigNozSzFailure(dict[str, Any]):
    """Structured sz failure that remains falsey for legacy callers."""

    def __bool__(self) -> bool:
        return False


def signoz_mcp_enabled(cfg: Any | None = None) -> bool:
    effective_cfg = cfg if cfg is not None else get_config()
    return bool(mcp_endpoint(effective_cfg.signoz.mcp_url))


async def call_signoz_tool(
    name: str,
    arguments: dict[str, Any],
    *,
    cfg: Any | None = None,
) -> dict[str, Any]:
    """Call one remote SigNoz MCP tool and preserve structured failures."""
    effective_cfg = cfg if cfg is not None else get_config()
    service_cfg = effective_cfg.signoz
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
) -> list[Any] | dict[str, Any]:
    """Compatibility wrapper preserving remote failures as falsey mappings."""
    payload = await call_signoz_tool("sz_run", {"args": list(args)}, cfg=cfg)
    if not payload.get("success"):
        failure = dict(payload)
        failure.setdefault("success", False)
        failure.setdefault("code", "SIGNOZ_MCP_FAILED")
        failure.setdefault("error", "SigNoz MCP sz_run failed")
        return SigNozSzFailure(failure)
    data = payload.get("data")
    if isinstance(data, (list, dict)):
        return data
    return SigNozSzFailure(
        {
            "success": False,
            "code": "SIGNOZ_MCP_BAD_DATA",
            "error": "SigNoz MCP sz_run returned non-list/dict data",
        }
    )
