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
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(endpoint, json=payload, headers=headers)

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

    data = resp.json()
    return DbGatewayResult(
        success=bool(data.get("success")),
        csv=str(data.get("csv") or ""),
        row_count=int(data.get("row_count") or 0),
        elapsed_ms=int(data.get("elapsed_ms") or 0),
        truncated=bool(data.get("truncated")),
        code=str(data.get("code") or ""),
        error=data.get("error"),
    )
