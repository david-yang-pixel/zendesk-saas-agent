"""Remote MCP client wrapper for Zendesk support-ticket operations."""

from __future__ import annotations

import json
from typing import Any

import httpx

from zd_agent.config import get_config
from zd_agent.integrations.mcp_response import load_json_or_sse_response
from zd_agent.log import get_logger

logger = get_logger(module="integrations.zendesk_mcp")


def _mcp_endpoint(url: str) -> str:
    base = (url or "").rstrip("/")
    if not base:
        return ""
    return base if base.endswith("/mcp") else f"{base}/mcp"


def zendesk_mcp_enabled(cfg: Any | None = None) -> bool:
    cfg = cfg or get_config()
    return bool(_mcp_endpoint(cfg.zendesk_mcp.mcp_url))


async def call_zendesk_tool(
    name: str,
    arguments: dict[str, Any],
    *,
    cfg: Any | None = None,
) -> dict[str, Any]:
    """Call a remote Zendesk MCP tool and return its structured payload.

    Transport and protocol failures are normalized to ``success=false`` so tool
    callers can surface a precise error without silently falling back to the
    in-process Zendesk client.
    """
    cfg = cfg or get_config()
    zendesk_cfg = cfg.zendesk_mcp
    endpoint = _mcp_endpoint(zendesk_cfg.mcp_url)
    if not endpoint:
        return {
            "success": False,
            "code": "ZENDESK_MCP_URL_MISSING",
            "error": "ZENDESK_MCP_URL is not configured",
        }

    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }
    if zendesk_cfg.mcp_api_key:
        headers["Authorization"] = f"Bearer {zendesk_cfg.mcp_api_key}"

    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }

    try:
        async with httpx.AsyncClient(
            timeout=zendesk_cfg.request_timeout_seconds + 5,
            trust_env=False,
        ) as client:
            resp = await client.post(endpoint, json=payload, headers=headers)
    except Exception as e:
        logger.warning("zendesk_mcp_request_failed", tool=name, error=str(e))
        return {"success": False, "code": "ZENDESK_MCP_REQUEST_FAILED", "error": str(e)}

    if resp.status_code == 401:
        logger.warning("zendesk_mcp_unauthorized", tool=name)
        return {
            "success": False,
            "code": "ZENDESK_MCP_UNAUTHORIZED",
            "error": "unauthorized",
        }
    if resp.status_code >= 400:
        logger.warning(
            "zendesk_mcp_http_error",
            tool=name,
            status=resp.status_code,
            body=resp.text[:500],
        )
        return {
            "success": False,
            "code": "ZENDESK_MCP_HTTP_ERROR",
            "error": f"HTTP {resp.status_code}: {resp.text[:500]}",
        }

    try:
        data = load_json_or_sse_response(resp, context="Zendesk MCP")
    except RuntimeError as e:
        logger.warning("zendesk_mcp_parse_failed", tool=name, error=str(e))
        return {"success": False, "code": "ZENDESK_MCP_PARSE_FAILED", "error": str(e)}
    if "error" in data:
        logger.warning("zendesk_mcp_jsonrpc_error", tool=name, error=str(data["error"]))
        return {
            "success": False,
            "code": "ZENDESK_MCP_JSONRPC_ERROR",
            "error": str(data["error"]),
        }

    raw = data.get("result", {})
    if not isinstance(raw, dict):
        return {
            "success": False,
            "code": "ZENDESK_MCP_BAD_RESULT",
            "error": "Zendesk MCP returned non-object result",
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
            "code": "ZENDESK_MCP_BAD_CONTENT",
            "error": "Zendesk MCP returned no text content",
        }
    first = content[0]
    if not isinstance(first, dict) or first.get("type") != "text":
        return {
            "success": False,
            "code": "ZENDESK_MCP_BAD_CONTENT",
            "error": "Zendesk MCP returned invalid text content",
        }
    try:
        payload = json.loads(first.get("text") or "{}")
    except json.JSONDecodeError as e:
        return {"success": False, "code": "ZENDESK_MCP_BAD_JSON", "error": str(e)}
    if isinstance(payload, dict):
        return payload
    return {
        "success": False,
        "code": "ZENDESK_MCP_BAD_PAYLOAD",
        "error": "Zendesk MCP text payload is not an object",
    }
