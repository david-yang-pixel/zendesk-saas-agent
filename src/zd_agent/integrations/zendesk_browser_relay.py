"""HTTP client for the Zendesk browser relay.

The relay is used for SaaS native messaging tickets where ordinary Support API
ticket comments are not visible in the customer widget and Conversations API is
not available.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any
from urllib.parse import urlparse

import httpx

from zd_agent.integrations.mcp_response import (
    expect_json_object,
    load_json_or_sse_response,
)


@dataclass(frozen=True)
class BrowserRelaySendResult:
    ok: bool
    ticket_id: int
    status: str
    current_url: str | None = None
    error: str | None = None


class BrowserRelayDefiniteUnsentError(RuntimeError):
    """The relay proved that it failed before clicking Zendesk Send."""


class BrowserRelayStaleContextError(BrowserRelayDefiniteUnsentError):
    """The relay definitely aborted before clicking Send due to a newer message."""


class BrowserRelayAmbiguousReceiptError(RuntimeError):
    """A relay claims success without proving it sent on the requested ticket."""

    def __init__(self, message: str, *, receipt: BrowserRelaySendResult) -> None:
        super().__init__(message)
        self.receipt = receipt


def _safe_error_text(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return text[:300]


def _mcp_text_content(value: Any) -> str:
    if not isinstance(value, list):
        return ""
    parts: list[str] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        if item.get("type") != "text":
            continue
        text = str(item.get("text") or "").strip()
        if text:
            parts.append(text)
    return "\n".join(parts)


def _coerce_ticket_id(value: Any, *, fallback: int, context: str) -> int:
    candidate = fallback if value in (None, "") else value
    if isinstance(candidate, bool):
        raise RuntimeError(f"{context} invalid ticket_id: {candidate!r}")
    try:
        ticket_id = int(candidate)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"{context} invalid ticket_id: {candidate!r}") from exc
    if ticket_id < 1:
        raise RuntimeError(f"{context} invalid ticket_id: {candidate!r}")
    return ticket_id


def _receipt_ticket_id(
    value: Any,
    *,
    requested_ticket_id: int,
    ok: bool,
    status: str,
    context: str,
) -> int:
    if ok and status == "sent" and value in (None, ""):
        return 0
    return _coerce_ticket_id(
        value,
        fallback=requested_ticket_id,
        context=context,
    )


def _uses_mcp_endpoint(base_url: str) -> bool:
    lowered = base_url.lower().rstrip("/")
    return lowered.endswith("/mcp") or lowered.endswith("/zendesk-reply-mcp")


def _ticket_id_from_current_url(value: str | None) -> int | None:
    if not value:
        return None
    try:
        path = urlparse(value).path
    except ValueError:
        return None
    match = re.search(r"/agent/tickets/(\d+)(?:/|$)", path)
    return int(match.group(1)) if match else None


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
        raise BrowserRelayDefiniteUnsentError(
            "Zendesk browser relay URL is not configured"
        )
    if not api_key:
        raise BrowserRelayDefiniteUnsentError(
            "Zendesk browser relay API key is not configured"
        )

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
        return _ensure_sent(result, requested_ticket_id=ticket_id)

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
        raise BrowserRelayDefiniteUnsentError("Zendesk browser relay rejected API key")
    if response.status_code >= 400:
        raise RuntimeError(
            "Zendesk browser relay HTTP "
            f"{response.status_code}: {_safe_error_text(response.text)}"
        )

    data = load_json_or_sse_response(response, context="Zendesk browser relay")

    response_ok = bool(data.get("ok"))
    response_status = str(data.get("status") or "unknown")
    return _ensure_sent(
        BrowserRelaySendResult(
            ok=response_ok,
            ticket_id=_receipt_ticket_id(
                data.get("ticket_id"),
                requested_ticket_id=ticket_id,
                ok=response_ok,
                status=response_status,
                context="Zendesk browser relay response",
            ),
            status=response_status,
            current_url=data.get("current_url") or None,
            error=data.get("error") or None,
        ),
        requested_ticket_id=ticket_id,
    )


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
        arguments["snapshot_customer_last_message_id"] = (
            snapshot_customer_last_message_id
        )

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
        raise BrowserRelayDefiniteUnsentError("Zendesk browser relay rejected API key")
    if response.status_code >= 400:
        raise RuntimeError(
            "Zendesk browser relay MCP HTTP "
            f"{response.status_code}: {_safe_error_text(response.text)}"
        )

    data = load_json_or_sse_response(response, context="Zendesk browser relay MCP")

    if "error" in data:
        raise RuntimeError(
            f"Zendesk browser relay MCP error: {_safe_error_text(data.get('error'))}"
        )

    result = expect_json_object(
        data.get("result") or {},
        context="Zendesk browser relay MCP result",
    )
    structured = expect_json_object(
        result.get("structuredContent") or {},
        context="Zendesk browser relay MCP structuredContent",
    )
    is_error = bool(result.get("isError"))
    error_text = structured.get("error")
    if is_error and not error_text:
        error_text = _mcp_text_content(result.get("content"))
    response_ok = bool(structured.get("ok")) and not is_error
    response_status = str(
        structured.get("status") or ("mcp_tool_error" if is_error else "unknown")
    )
    return BrowserRelaySendResult(
        ok=response_ok,
        ticket_id=_receipt_ticket_id(
            structured.get("ticket_id"),
            requested_ticket_id=ticket_id,
            ok=response_ok,
            status=response_status,
            context="Zendesk browser relay MCP response",
        ),
        status=response_status,
        current_url=structured.get("current_url") or None,
        error=error_text or None,
    )


def _ensure_sent(
    result: BrowserRelaySendResult,
    *,
    requested_ticket_id: int | None = None,
) -> BrowserRelaySendResult:
    if result.ok and result.status == "sent":
        current_url_ticket_id = _ticket_id_from_current_url(result.current_url)
        if requested_ticket_id is not None and (
            result.ticket_id != requested_ticket_id
            or current_url_ticket_id != requested_ticket_id
        ):
            raise BrowserRelayAmbiguousReceiptError(
                "Zendesk browser relay success receipt does not bind to the "
                "requested ticket "
                f"request={requested_ticket_id} receipt={result.ticket_id} "
                f"current_url_ticket={current_url_ticket_id}",
                receipt=result,
            )
        return result
    if result.status == "login_required":
        raise BrowserRelayDefiniteUnsentError("Zendesk browser relay login_required")
    if result.status == "stale_context":
        raise BrowserRelayStaleContextError(
            "Zendesk browser relay aborted stale_context"
        )
    if result.status == "freshness_check_failed":
        raise BrowserRelayDefiniteUnsentError(
            "Zendesk browser relay freshness_check_failed"
        )
    raise RuntimeError(
        "Zendesk browser relay send failed"
        f" status={result.status} error={_safe_error_text(result.error)}"
    )
