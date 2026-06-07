"""MCP client wrapper for DB Gateway."""
from __future__ import annotations

import json
from typing import Any

from zd_agent.integrations.db_gateway import DbGatewayResult
from zd_agent.integrations.mcp_subprocess import MCPClientError, MCPSubprocessClient


_client: MCPSubprocessClient | None = None


def _default_mcp_binary() -> str:
    return "db-gateway-mcp"


def _make_client(cfg: Any) -> MCPSubprocessClient:
    global _client
    if _client is not None:
        return _client

    binary = cfg.db_gateway.mcp_binary or _default_mcp_binary()
    _client = MCPSubprocessClient(
        binary=binary,
        env={
            "DB_GATEWAY_URL": cfg.db_gateway.url,
            "DB_GATEWAY_API_KEY": cfg.db_gateway.api_key,
            "DB_GATEWAY_TIMEOUT": str(cfg.db_gateway.request_timeout_seconds),
        },
        client_name="zd-agent-db-gateway",
    )
    return _client


async def query_db_gateway_mcp(
    *,
    cfg: Any,
    database: str,
    sql: str,
    ticket_id: str,
) -> DbGatewayResult:
    client = _make_client(cfg)
    try:
        raw = await client.call_tool(
            "db_gateway_query",
            {"database": database, "sql": sql, "ticket_id": ticket_id},
            timeout=cfg.db_gateway.request_timeout_seconds + 5,
        )
    except MCPClientError as e:
        return DbGatewayResult(success=False, code="MCP_ERROR", error=str(e))

    content = raw.get("content", [])
    if not content or content[0].get("type") != "text":
        return DbGatewayResult(
            success=False,
            code="MCP_BAD_RESPONSE",
            error=f"unexpected MCP response: {raw}",
        )
    try:
        data = json.loads(content[0].get("text") or "{}")
    except json.JSONDecodeError as e:
        return DbGatewayResult(
            success=False,
            code="MCP_BAD_JSON",
            error=f"invalid MCP JSON response: {e}",
        )

    return DbGatewayResult(
        success=bool(data.get("success")),
        csv=str(data.get("csv") or ""),
        row_count=int(data.get("row_count") or 0),
        elapsed_ms=int(data.get("elapsed_ms") or 0),
        truncated=bool(data.get("truncated")),
        code=str(data.get("code") or ""),
        error=data.get("error"),
    )


async def shutdown_db_gateway_mcp() -> None:
    global _client
    if _client is not None:
        await _client.close()
        _client = None
