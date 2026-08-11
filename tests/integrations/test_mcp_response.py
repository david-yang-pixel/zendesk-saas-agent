"""Tests for remote MCP JSON/SSE response parsing."""

from __future__ import annotations

import httpx

from zd_agent.integrations.mcp_response import load_json_or_sse_response


def test_load_json_or_sse_response_parses_json_object() -> None:
    response = httpx.Response(200, json={"result": {"ok": True}})

    data = load_json_or_sse_response(response, context="MCP")

    assert data == {"result": {"ok": True}}


def test_load_json_or_sse_response_parses_crlf_sse_events() -> None:
    response = httpx.Response(
        200,
        headers={"Content-Type": "text/event-stream"},
        text=(
            "event: ping\r\n"
            'data: {"jsonrpc":"2.0","id":1}\r\n'
            "\r\n"
            "event: message\r\n"
            'data: {"jsonrpc":"2.0","id":1,"result":{"ok":true}}\r\n'
            "\r\n"
        ),
    )

    data = load_json_or_sse_response(response, context="MCP")

    assert data == {"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}
