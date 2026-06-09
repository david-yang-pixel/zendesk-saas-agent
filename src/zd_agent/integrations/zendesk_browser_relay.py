"""HTTP client for the Zendesk browser relay.

The relay is used for SaaS native messaging tickets where ordinary Support API
ticket comments are not visible in the customer widget and Conversations API is
not available.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx


@dataclass(frozen=True)
class BrowserRelaySendResult:
    ok: bool
    ticket_id: int
    status: str
    current_url: str | None = None
    error: str | None = None


def _safe_error_text(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return text[:300]


def _uses_mcp_endpoint(base_url: str) -> bool:
    lowered = base_url.lower().rstrip("/")
    return lowered.endswith("/mcp") or lowered.endswith("/zendesk-reply-mcp")


def _expect_json_object(value: Any, *, context: str) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    raise RuntimeError(f"{context} expected JSON object, got {type(value).__name__}")


async def send_browser_relay_reply(
    *,
    url: str,
    api_key: str,
    ticket_id: int,
    body: str,
    timeout_seconds: int = 90,
    freshness_check_url: str | None = None,
    freshness_check_api_key: str | None = None,
    snapshot_customer_message_count: int | None = None,
    snapshot_customer_last_message_id: str | None = None,
) -> BrowserRelaySendResult:
    """Send a public reply through the Zendesk browser relay."""
    base_url = (url or "").strip().rstrip("/")
    api_key = (api_key or "").strip()
    if not base_url:
        raise RuntimeError("Zendesk browser relay URL is not configured")
    if not api_key:
        raise RuntimeError("Zendesk browser relay API key is not configured")

    if _uses_mcp_endpoint(base_url):
        result = await _send_via_mcp(
            endpoint=base_url,
            api_key=api_key,
            ticket_id=ticket_id,
            body=body,
            timeout_seconds=timeout_seconds,
            freshness_check_url=freshness_check_url,
            freshness_check_api_key=freshness_check_api_key,
            snapshot_customer_message_count=snapshot_customer_message_count,
            snapshot_customer_last_message_id=snapshot_customer_last_message_id,
        )
        return _ensure_sent(result)

    endpoint = f"{base_url}/send"
    headers = {"Authorization": f"Bearer {api_key}"}
    payload: dict[str, Any] = {"ticket_id": ticket_id, "body": body}
    if freshness_check_url:
        payload["freshness_check_url"] = freshness_check_url
    if freshness_check_api_key:
        payload["freshness_check_api_key"] = freshness_check_api_key
    if snapshot_customer_message_count is not None:
        payload["snapshot_customer_message_count"] = snapshot_customer_message_count
    if snapshot_customer_last_message_id:
        payload["snapshot_customer_last_message_id"] = snapshot_customer_last_message_id

    try:
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            response = await client.post(endpoint, headers=headers, json=payload)
    except httpx.TimeoutException as exc:
        raise RuntimeError("Zendesk browser relay request timed out") from exc
    except httpx.HTTPError as exc:
        raise RuntimeError(
            f"Zendesk browser relay request failed: {type(exc).__name__}"
        ) from exc

    if response.status_code in (401, 403):
        raise RuntimeError("Zendesk browser relay rejected API key")
    if response.status_code >= 400:
        raise RuntimeError(
            "Zendesk browser relay HTTP "
            f"{response.status_code}: {_safe_error_text(response.text)}"
        )

    try:
        data = response.json()
    except ValueError as exc:
        raise RuntimeError("Zendesk browser relay returned invalid JSON") from exc
    data = _expect_json_object(data, context="Zendesk browser relay")

    return _ensure_sent(BrowserRelaySendResult(
        ok=bool(data.get("ok")),
        ticket_id=int(data.get("ticket_id") or ticket_id),
        status=str(data.get("status") or "unknown"),
        current_url=data.get("current_url") or None,
        error=data.get("error") or None,
    ))


async def _send_via_mcp(
    *,
    endpoint: str,
    api_key: str,
    ticket_id: int,
    body: str,
    timeout_seconds: int,
    freshness_check_url: str | None,
    freshness_check_api_key: str | None,
    snapshot_customer_message_count: int | None,
    snapshot_customer_last_message_id: str | None,
) -> BrowserRelaySendResult:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }
    arguments: dict[str, Any] = {"ticket_id": ticket_id, "body": body}
    if freshness_check_url:
        arguments["freshness_check_url"] = freshness_check_url
    if freshness_check_api_key:
        arguments["freshness_check_api_key"] = freshness_check_api_key
    if snapshot_customer_message_count is not None:
        arguments["snapshot_customer_message_count"] = snapshot_customer_message_count
    if snapshot_customer_last_message_id:
        arguments["snapshot_customer_last_message_id"] = snapshot_customer_last_message_id

    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "send_zendesk_native_message",
            "arguments": arguments,
        },
    }
    try:
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            response = await client.post(endpoint, headers=headers, json=payload)
    except httpx.TimeoutException as exc:
        raise RuntimeError("Zendesk browser relay MCP request timed out") from exc
    except httpx.HTTPError as exc:
        raise RuntimeError(
            f"Zendesk browser relay MCP request failed: {type(exc).__name__}"
        ) from exc

    if response.status_code in (401, 403):
        raise RuntimeError("Zendesk browser relay rejected API key")
    if response.status_code >= 400:
        raise RuntimeError(
            "Zendesk browser relay MCP HTTP "
            f"{response.status_code}: {_safe_error_text(response.text)}"
        )

    try:
        data = response.json()
    except ValueError as exc:
        raise RuntimeError("Zendesk browser relay MCP returned invalid JSON") from exc
    data = _expect_json_object(data, context="Zendesk browser relay MCP")

    if "error" in data:
        raise RuntimeError(
            "Zendesk browser relay MCP error: "
            f"{_safe_error_text(data.get('error'))}"
        )

    result = _expect_json_object(
        data.get("result") or {},
        context="Zendesk browser relay MCP result",
    )
    structured = _expect_json_object(
        result.get("structuredContent") or {},
        context="Zendesk browser relay MCP structuredContent",
    )
    return BrowserRelaySendResult(
        ok=bool(structured.get("ok")) and not bool(result.get("isError")),
        ticket_id=int(structured.get("ticket_id") or ticket_id),
        status=str(structured.get("status") or "unknown"),
        current_url=structured.get("current_url") or None,
        error=structured.get("error") or None,
    )


def _ensure_sent(result: BrowserRelaySendResult) -> BrowserRelaySendResult:
    if result.ok and result.status == "sent":
        return result
    if result.status == "login_required":
        raise RuntimeError("Zendesk browser relay login_required")
    if result.status == "stale_context":
        raise RuntimeError("Zendesk browser relay aborted stale_context")
    if result.status == "freshness_check_failed":
        raise RuntimeError("Zendesk browser relay freshness_check_failed")
    raise RuntimeError(
        "Zendesk browser relay send failed"
        f" status={result.status} error={_safe_error_text(result.error)}"
    )
