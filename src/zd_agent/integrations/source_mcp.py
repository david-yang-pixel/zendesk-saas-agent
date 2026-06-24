"""Remote MCP client wrapper for SaaS source-code queries."""
from __future__ import annotations

import json
from typing import Any

import httpx

from zd_agent.config import get_config
from zd_agent.integrations.mcp_response import load_json_or_sse_response
from zd_agent.log import get_logger

logger = get_logger(module="integrations.source_mcp")


def _mcp_endpoint(url: str) -> str:
    base = (url or "").rstrip("/")
    if not base:
        return ""
    return base if base.endswith("/mcp") else f"{base}/mcp"


def source_mcp_enabled(cfg: Any | None = None) -> bool:
    cfg = cfg or get_config()
    return bool(_mcp_endpoint(cfg.source_mcp.mcp_url))


async def call_source_tool(
    name: str,
    arguments: dict[str, Any],
    *,
    cfg: Any | None = None,
) -> dict[str, Any]:
    """Call a remote source MCP tool and return its structured payload.

    The return shape is the source MCP payload itself:
    ``{"success": true, "data": ...}`` or
    ``{"success": false, "code": "...", "error": "..."}``.
    Transport/protocol errors are normalized to ``success=false`` so source
    tools can surface a precise failure to the LLM.
    """
    cfg = cfg or get_config()
    source_cfg = cfg.source_mcp
    endpoint = _mcp_endpoint(source_cfg.mcp_url)
    if not endpoint:
        return {
            "success": False,
            "code": "SOURCE_MCP_URL_MISSING",
            "error": "SOURCE_MCP_URL is not configured",
        }

    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }
    if source_cfg.mcp_api_key:
        headers["Authorization"] = f"Bearer {source_cfg.mcp_api_key}"

    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }

    try:
        async with httpx.AsyncClient(
            timeout=source_cfg.request_timeout_seconds + 5,
            trust_env=False,
        ) as client:
            resp = await client.post(endpoint, json=payload, headers=headers)
    except Exception as e:
        logger.warning("source_mcp_request_failed", tool=name, error=str(e))
        return {"success": False, "code": "SOURCE_MCP_REQUEST_FAILED", "error": str(e)}

    if resp.status_code == 401:
        logger.warning("source_mcp_unauthorized", tool=name)
        return {"success": False, "code": "SOURCE_MCP_UNAUTHORIZED", "error": "unauthorized"}
    if resp.status_code >= 400:
        logger.warning(
            "source_mcp_http_error",
            tool=name,
            status=resp.status_code,
            body=resp.text[:500],
        )
        return {
            "success": False,
            "code": "SOURCE_MCP_HTTP_ERROR",
            "error": f"HTTP {resp.status_code}: {resp.text[:500]}",
        }

    try:
        data = load_json_or_sse_response(resp, context="Source MCP")
    except RuntimeError as e:
        logger.warning("source_mcp_parse_failed", tool=name, error=str(e))
        return {"success": False, "code": "SOURCE_MCP_PARSE_FAILED", "error": str(e)}
    if "error" in data:
        logger.warning("source_mcp_jsonrpc_error", tool=name, error=str(data["error"]))
        return {
            "success": False,
            "code": "SOURCE_MCP_JSONRPC_ERROR",
            "error": str(data["error"]),
        }

    raw = data.get("result", {})
    if not isinstance(raw, dict):
        return {
            "success": False,
            "code": "SOURCE_MCP_BAD_RESULT",
            "error": "Source MCP returned non-object result",
        }

    structured = raw.get("structuredContent")
    if isinstance(structured, dict):
        return structured
    return _parse_text_payload(raw)


def _parse_text_payload(raw: dict[str, Any]) -> dict[str, Any]:
    content = raw.get("content", [])
    if not isinstance(content, list) or not content:
        return {
            "success": False,
            "code": "SOURCE_MCP_BAD_CONTENT",
            "error": "Source MCP returned no text content",
        }
    first = content[0]
    if not isinstance(first, dict) or first.get("type") != "text":
        return {
            "success": False,
            "code": "SOURCE_MCP_BAD_CONTENT",
            "error": "Source MCP returned invalid text content",
        }
    try:
        payload = json.loads(first.get("text") or "{}")
    except json.JSONDecodeError as e:
        return {"success": False, "code": "SOURCE_MCP_BAD_JSON", "error": str(e)}
    if isinstance(payload, dict):
        return payload
    return {
        "success": False,
        "code": "SOURCE_MCP_BAD_PAYLOAD",
        "error": "Source MCP text payload is not an object",
    }
