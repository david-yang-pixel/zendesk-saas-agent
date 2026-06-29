"""Remote sandbox-worker MCP wrapper."""

from __future__ import annotations

from typing import Any

from zd_agent.config import get_config
from zd_agent.integrations.tool_service_mcp import call_mcp_tool, mcp_endpoint

SERVICE = "sandbox-worker"


def sandbox_worker_enabled(cfg: Any | None = None) -> bool:
    cfg = cfg or get_config()
    return bool(mcp_endpoint(cfg.sandbox_worker.mcp_url))


async def call_sandbox_worker_tool(
    name: str,
    arguments: dict[str, Any],
    *,
    session: Any,
    cfg: Any | None = None,
) -> dict[str, Any]:
    cfg = cfg or get_config()
    service_cfg = cfg.sandbox_worker
    enriched = {
        **arguments,
        "ticket_id": getattr(session, "ticket_id", None),
        "session_id": getattr(session, "session_id", ""),
        "sandbox_dir": (
            str(getattr(session, "sandbox_dir", "") or "")
        ),
    }
    return await call_mcp_tool(
        service=SERVICE,
        mcp_url=service_cfg.mcp_url,
        mcp_api_key=service_cfg.mcp_api_key,
        request_timeout_seconds=service_cfg.request_timeout_seconds,
        name=name,
        arguments=enriched,
        missing_code="SANDBOX_WORKER_URL_MISSING",
    )
