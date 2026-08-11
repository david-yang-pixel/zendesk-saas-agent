"""Helpers for parsing remote MCP JSON or SSE responses."""

from __future__ import annotations

import json
from typing import Any

import httpx


def expect_json_object(value: Any, *, context: str) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    raise RuntimeError(f"{context} expected JSON object, got {type(value).__name__}")


def load_json_or_sse_response(
    response: httpx.Response, *, context: str
) -> dict[str, Any]:
    """Parse either ordinary JSON or streamable HTTP/SSE MCP responses."""
    content_type = (response.headers.get("Content-Type") or "").lower()
    if "text/event-stream" not in content_type:
        try:
            data = response.json()
        except ValueError as exc:
            raise RuntimeError(f"{context} returned invalid JSON") from exc
        return expect_json_object(data, context=context)

    parsed: dict[str, Any] | None = None
    text = response.text.replace("\r\n", "\n").replace("\r", "\n")
    for block in text.split("\n\n"):
        data_lines = [
            line.partition(":")[2].lstrip()
            for line in block.splitlines()
            if line.startswith("data:")
        ]
        if not data_lines:
            continue
        payload = "\n".join(data_lines).strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            candidate = json.loads(payload)
        except ValueError:
            continue
        parsed = expect_json_object(candidate, context=context)
        if "result" in parsed or "error" in parsed:
            return parsed
    if parsed is not None:
        return parsed
    raise RuntimeError(f"{context} SSE response did not contain a JSON object")
