"""Remote MCP client wrapper for SigNoz sz execution."""
from __future__ import annotations

import json
from typing import Any

import httpx

from zd_agent.config import get_config
from zd_agent.integrations.mcp_response import load_json_or_sse_response
from zd_agent.log import get_logger

logger = get_logger(module="integrations.signoz_mcp")


def _mcp_endpoint(url: str) -> str:
    base = (url or "").rstrip("/")
    if not base:
        return ""
    if base.endswith("/mcp"):
        return base
    return f"{base}/mcp"


def _parse_tool_result(raw: dict[str, Any]) -> list[Any] | dict[str, Any] | None:
    content = raw.get("content", [])
    if not isinstance(content, list) or not content:
        logger.warning("signoz_mcp_bad_response", raw=raw)
        return None
    first_content = content[0]
    if not isinstance(first_content, dict) or first_content.get("type") != "text":
        logger.warning("signoz_mcp_bad_content", raw=raw)
        return None
    try:
        data = json.loads(first_content.get("text") or "{}")
    except json.JSONDecodeError as e:
        logger.warning("signoz_mcp_bad_json", error=str(e))
        return None
    if not isinstance(data, dict):
        logger.warning("signoz_mcp_bad_result_shape", data=data)
        return None
    if not data.get("success"):
        logger.warning(
            "signoz_mcp_tool_failed",
            code=data.get("code"),
            error=data.get("error"),
        )
        return None
    payload = data.get("data")
    if isinstance(payload, (list, dict)):
        return payload
    return None


async def run_signoz_sz(
    *args: str,
    cfg: Any | None = None,
) -> list[Any] | dict[str, Any] | None:
    """Run sz through the remote SigNoz MCP service.

    Returns parsed sz JSON data on success. Failures are intentionally reduced
    to None so tool callers keep the previous best-effort behavior.
    """
    cfg = cfg or get_config()
    signoz_cfg = cfg.signoz
    endpoint = _mcp_endpoint(signoz_cfg.mcp_url)
    if not endpoint:
        logger.warning("signoz_mcp_url_missing")
        return None

    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }
    if signoz_cfg.mcp_api_key:
        headers["Authorization"] = f"Bearer {signoz_cfg.mcp_api_key}"

    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "sz_run",
            "arguments": {"args": list(args)},
        },
    }

    try:
        async with httpx.AsyncClient(
            timeout=signoz_cfg.request_timeout_seconds + 5,
            trust_env=False,
        ) as client:
            resp = await client.post(endpoint, json=payload, headers=headers)
    except Exception as e:
        logger.warning("signoz_mcp_request_failed", args=args, error=str(e))
        return None

    if resp.status_code == 401:
        logger.warning("signoz_mcp_unauthorized")
        return None
    if resp.status_code >= 400:
        logger.warning(
            "signoz_mcp_http_error",
            status=resp.status_code,
            body=resp.text[:500],
        )
        return None

    try:
        data = load_json_or_sse_response(resp, context="SigNoz MCP")
    except RuntimeError as e:
        logger.warning("signoz_mcp_parse_failed", error=str(e))
        return None
    if "error" in data:
        logger.warning("signoz_mcp_jsonrpc_error", error=str(data["error"]))
        return None
    raw = data.get("result", {})
    if not isinstance(raw, dict):
        logger.warning("signoz_mcp_bad_jsonrpc_result", data=data)
        return None
    return _parse_tool_result(raw)
