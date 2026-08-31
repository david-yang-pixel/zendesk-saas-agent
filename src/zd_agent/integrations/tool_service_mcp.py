"""Shared Streamable HTTP MCP client for sibling Tool Services."""

from __future__ import annotations

import json
from typing import Any

import httpx

from zd_agent.integrations.mcp_response import load_json_or_sse_response
from zd_agent.log import get_logger

logger = get_logger(module="integrations.tool_service_mcp")


def _extract_json_object_text_payload(
    raw: dict[str, Any],
) -> tuple[dict[str, Any] | None, str | None, str]:
    content = raw.get("content", [])
    if not isinstance(content, list) or not content:
        return None, "BAD_CONTENT", "returned no text content"

    objects: list[dict[str, Any]] = []
    saw_text = False
    saw_non_object = False
    last_json_error = ""
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "text":
            continue
        text = block.get("text")
        if not isinstance(text, str):
            continue
        saw_text = True
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as error:
            last_json_error = str(error)
            continue
        if isinstance(payload, dict):
            objects.append(payload)
        else:
            saw_non_object = True

    if len(objects) == 1:
        return objects[0], None, ""
    if len(objects) > 1:
        return (
            None,
            "AMBIGUOUS_CONTENT",
            "returned ambiguous multiple JSON object text payloads",
        )
    if not saw_text:
        return None, "BAD_CONTENT", "returned no valid text content"
    if saw_non_object:
        return None, "BAD_PAYLOAD", "text payload is not an object"
    return None, "BAD_JSON", last_json_error or "text payload is not valid JSON"


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
    payload, error_code, error = _extract_json_object_text_payload(raw)
    if payload is not None:
        return payload
    return {
        "success": False,
        "code": f"{prefix}_{error_code}",
        "error": f"{service} MCP {error}",
    }
