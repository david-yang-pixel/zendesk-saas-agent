"""MCP client wrapper for DB Gateway."""
from __future__ import annotations

import json
from typing import Any

import httpx

from zd_agent.integrations.db_gateway import DbGatewayResult
from zd_agent.integrations.mcp_response import load_json_or_sse_response


def _mcp_endpoint(url: str) -> str:
    base = (url or "").rstrip("/")
    if base.endswith("/mcp"):
        return base
    return f"{base}/mcp"


def _parse_tool_result(raw: dict[str, Any]) -> DbGatewayResult:
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


async def query_db_gateway_mcp(
    *,
    cfg: Any,
    database: str,
    sql: str,
    ticket_id: str,
) -> DbGatewayResult:
    endpoint = _mcp_endpoint(cfg.db_gateway.url)
    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }
    if cfg.db_gateway.api_key:
        headers["Authorization"] = f"Bearer {cfg.db_gateway.api_key}"

    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "db_gateway_query",
            "arguments": {
                "database": database,
                "sql": sql,
                "ticket_id": ticket_id,
            },
        },
    }
    try:
        async with httpx.AsyncClient(
            timeout=cfg.db_gateway.request_timeout_seconds + 5,
        ) as client:
            resp = await client.post(endpoint, json=payload, headers=headers)
    except Exception as e:
        return DbGatewayResult(success=False, code="MCP_ERROR", error=str(e))

    if resp.status_code == 401:
        return DbGatewayResult(
            success=False,
            code="UNAUTHORIZED",
            error="DB Gateway MCP rejected the API key",
        )
    if resp.status_code >= 400:
        return DbGatewayResult(
            success=False,
            code="MCP_HTTP_ERROR",
            error=f"DB Gateway MCP HTTP {resp.status_code}: {resp.text[:500]}",
        )

    try:
        data = load_json_or_sse_response(resp, context="DB Gateway MCP")
    except RuntimeError as e:
        err = str(e)
        if "expected JSON object" in err:
            return DbGatewayResult(
                success=False,
                code="MCP_BAD_RESPONSE",
                error=f"unexpected MCP response: {err}",
            )
        return DbGatewayResult(
            success=False,
            code="MCP_BAD_JSON",
            error=f"invalid MCP JSON response: {err}",
        )
    if "error" in data:
        return DbGatewayResult(
            success=False,
            code="MCP_ERROR",
            error=str(data["error"]),
        )
    raw = data.get("result", {})
    if not isinstance(raw, dict):
        return DbGatewayResult(
            success=False,
            code="MCP_BAD_RESPONSE",
            error=f"unexpected MCP response: {data}",
        )
    return _parse_tool_result(raw)


async def shutdown_db_gateway_mcp() -> None:
    return None
