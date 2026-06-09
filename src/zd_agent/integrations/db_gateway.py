"""HTTP client for the standalone DB Gateway service."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx


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


async def query_db_gateway(
    *,
    url: str,
    api_key: str,
    database: str,
    sql: str,
    ticket_id: str,
    timeout: int,
) -> DbGatewayResult:
    endpoint = f"{url.rstrip('/')}/query"
    headers = {"X-API-Key": api_key} if api_key else {}
    payload: dict[str, Any] = {
        "database": database,
        "sql": sql,
        "ticket_id": ticket_id,
    }
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(endpoint, json=payload, headers=headers)
    except httpx.TimeoutException as e:
        return DbGatewayResult(
            success=False,
            code="TIMEOUT",
            error=f"DB gateway request timed out: {e}",
        )
    except httpx.HTTPError as e:
        return DbGatewayResult(
            success=False,
            code="REQUEST_ERROR",
            error=f"DB gateway request failed: {e}",
        )

    if resp.status_code == 401:
        return DbGatewayResult(
            success=False,
            code="UNAUTHORIZED",
            error="DB gateway rejected the API key",
        )
    if resp.status_code >= 400:
        return DbGatewayResult(
            success=False,
            code="HTTP_ERROR",
            error=f"DB gateway HTTP {resp.status_code}: {resp.text[:500]}",
        )

    try:
        data = resp.json()
    except ValueError as e:
        return DbGatewayResult(
            success=False,
            code="BAD_JSON",
            error=f"Invalid DB gateway JSON response: {e}",
        )
    if not isinstance(data, dict):
        return DbGatewayResult(
            success=False,
            code="BAD_RESPONSE",
            error=f"Expected DB gateway JSON object, got {type(data).__name__}",
        )
    row_count = _coerce_int(data.get("row_count"))
    elapsed_ms = _coerce_int(data.get("elapsed_ms"))
    if row_count is None or elapsed_ms is None:
        return DbGatewayResult(
            success=False,
            code="BAD_RESPONSE",
            error=(
                "Invalid DB gateway numeric field: "
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
