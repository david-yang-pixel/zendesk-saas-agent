"""Shared Streamable HTTP MCP client for sibling Tool Services."""

from __future__ import annotations

import json
from typing import Any

import httpx

from zd_agent.integrations.mcp_response import load_json_or_sse_response
from zd_agent.log import get_logger

logger = get_logger(module="integrations.tool_service_mcp")


def mcp_endpoint(url: str) -> str:
    base = (url or "").rstrip("/")
    if not base:
        return ""
    return base if base.endswith("/mcp") else f"{base}/mcp"


async def call_mcp_tool(
    *,
    service: str,
    mcp_url: str,
    mcp_api_key: str,
    request_timeout_seconds: int,
    name: str,
    arguments: dict[str, Any],
    missing_code: str,
) -> dict[str, Any]:
    """Call a remote MCP tool and return its structured payload.

    Tool-service wrappers keep their own service names and missing-url codes,
    but transport/protocol parsing is identical across GitHub, docs,
    notification, and sandbox worker services.
    """
    endpoint = mcp_endpoint(mcp_url)
    prefix = service.upper().replace("-", "_")
    if not endpoint:
        return {
            "success": False,
            "code": missing_code,
            "error": f"{service} MCP URL is not configured",
        }

    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }
    if mcp_api_key:
        headers["Authorization"] = f"Bearer {mcp_api_key}"

    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }

    try:
        async with httpx.AsyncClient(
            timeout=request_timeout_seconds + 5,
            trust_env=False,
        ) as client:
            resp = await client.post(endpoint, json=payload, headers=headers)
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "tool_service_mcp_request_failed",
            service=service,
            tool=name,
            error=str(e),
        )
        return {"success": False, "code": f"{prefix}_REQUEST_FAILED", "error": str(e)}

    if resp.status_code == 401:
        logger.warning("tool_service_mcp_unauthorized", service=service, tool=name)
        return {
            "success": False,
            "code": f"{prefix}_UNAUTHORIZED",
            "error": "unauthorized",
        }
    if resp.status_code >= 400:
        logger.warning(
            "tool_service_mcp_http_error",
            service=service,
            tool=name,
            status=resp.status_code,
            body=resp.text[:500],
        )
        return {
            "success": False,
            "code": f"{prefix}_HTTP_ERROR",
            "error": f"HTTP {resp.status_code}: {resp.text[:500]}",
        }

    try:
        data = load_json_or_sse_response(resp, context=f"{service} MCP")
    except RuntimeError as e:
        logger.warning(
            "tool_service_mcp_parse_failed",
            service=service,
            tool=name,
            error=str(e),
        )
        return {"success": False, "code": f"{prefix}_PARSE_FAILED", "error": str(e)}
    if "error" in data:
        logger.warning(
            "tool_service_mcp_jsonrpc_error",
            service=service,
            tool=name,
            error=str(data["error"]),
        )
        return {
            "success": False,
            "code": f"{prefix}_JSONRPC_ERROR",
            "error": str(data["error"]),
        }

    raw = data.get("result", {})
    if not isinstance(raw, dict):
        return {
            "success": False,
            "code": f"{prefix}_BAD_RESULT",
            "error": f"{service} MCP returned non-object result",
        }

    structured = raw.get("structuredContent")
    if isinstance(structured, dict):
        return structured
    return _parse_text_payload(raw, prefix=prefix, service=service)


def _parse_text_payload(
    raw: dict[str, Any],
    *,
    prefix: str,
    service: str,
) -> dict[str, Any]:
    content = raw.get("content", [])
    if not isinstance(content, list) or not content:
        return {
            "success": False,
            "code": f"{prefix}_BAD_CONTENT",
            "error": f"{service} MCP returned no text content",
        }
    first = content[0]
    if not isinstance(first, dict) or first.get("type") != "text":
        return {
            "success": False,
            "code": f"{prefix}_BAD_CONTENT",
            "error": f"{service} MCP returned invalid text content",
        }
    try:
        payload = json.loads(first.get("text") or "{}")
    except json.JSONDecodeError as e:
        return {"success": False, "code": f"{prefix}_BAD_JSON", "error": str(e)}
    if isinstance(payload, dict):
        return payload
    return {
        "success": False,
        "code": f"{prefix}_BAD_PAYLOAD",
        "error": f"{service} MCP text payload is not an object",
    }
