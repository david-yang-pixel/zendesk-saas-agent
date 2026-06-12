"""MCP client wrapper for DB Gateway."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx

from zd_agent.integrations.mcp_response import load_json_or_sse_response


@dataclass
class DbGatewayResult:
    success: bool
    csv: str = ""
    row_count: int = 0
    elapsed_ms: int = 0
    truncated: bool = False
    code: str = ""
    error: str | None = None


def _coerce_int(value: Any) -> int | None:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return None


def _mcp_endpoint(url: str) -> str:
    base = (url or "").rstrip("/")
    if base.endswith("/mcp"):
        return base
    return f"{base}/mcp"


def _parse_tool_result(raw: dict[str, Any]) -> DbGatewayResult:
    content = raw.get("content", [])
    if not isinstance(content, list) or not content:
        return DbGatewayResult(
            success=False,
            code="MCP_BAD_RESPONSE",
            error=f"unexpected MCP response: {raw}",
        )
    first_content = content[0]
    if not isinstance(first_content, dict) or first_content.get("type") != "text":
        return DbGatewayResult(
            success=False,
            code="MCP_BAD_RESPONSE",
            error=f"unexpected MCP response: {raw}",
        )
    try:
        data = json.loads(first_content.get("text") or "{}")
    except json.JSONDecodeError as e:
        return DbGatewayResult(
            success=False,
            code="MCP_BAD_JSON",
            error=f"invalid MCP JSON response: {e}",
        )
    if not isinstance(data, dict):
        return DbGatewayResult(
            success=False,
            code="MCP_BAD_RESPONSE",
            error=f"unexpected MCP tool result: {data}",
        )
    row_count = _coerce_int(data.get("row_count"))
    elapsed_ms = _coerce_int(data.get("elapsed_ms"))
    if row_count is None or elapsed_ms is None:
        return DbGatewayResult(
            success=False,
            code="MCP_BAD_RESPONSE",
            error=(
                "Invalid DB Gateway MCP numeric field: "
                f"row_count={data.get('row_count')!r} "
                f"elapsed_ms={data.get('elapsed_ms')!r}"
            ),
        )

    return DbGatewayResult(
        success=bool(data.get("success")),
        csv=str(data.get("csv") or ""),
        row_count=row_count,
        elapsed_ms=elapsed_ms,
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
