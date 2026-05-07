"""SigNoz HTTP API client (SigNoz Cloud v3 query API)."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

import httpx


_NOW_OFFSET_RE = re.compile(r"^now(?:\s*-\s*(\d+)\s*([smhd]))?$", re.IGNORECASE)
_UNIT_TO_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def to_epoch_ms(value: str | int) -> int:
    """规范化时间到 epoch ms 整数。

    支持：int / 全数字字符串 / ISO 8601 / "now" / "now-5m" / "now-1h" / "now-2d"
    """
    if isinstance(value, int):
        return value if value > 10**12 else value * 1000
    s = str(value).strip()
    if s.isdigit():
        return to_epoch_ms(int(s))
    m = _NOW_OFFSET_RE.match(s)
    if m:
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        amt, unit = m.group(1), m.group(2)
        if amt and unit:
            return now_ms - int(amt) * _UNIT_TO_SECONDS[unit.lower()] * 1000
        return now_ms
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    except ValueError:
        pass
    raise ValueError(
        f"signoz: 无法识别的时间格式 {value!r}；用 ISO 8601 / epoch ms / 'now-5m'"
    )


@dataclass
class SignozClient:
    url: str
    api_key: str
    timeout: float = 30.0

    def _headers(self) -> dict[str, str]:
        return {
            "SIGNOZ-API-KEY": self.api_key,
            "Content-Type": "application/json",
        }

    async def _query_range(self, body: dict[str, Any]) -> dict[str, Any]:
        endpoint = f"{self.url}/api/v3/query_range"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(endpoint, json=body, headers=self._headers())
        if resp.status_code != 200:
            raise RuntimeError(f"SigNoz query failed: {resp.status_code} {resp.text[:500]}")
        return resp.json()

    async def search_logs(
        self,
        *,
        service_name: str,
        severity: Optional[str] = None,
        query: Optional[str] = None,
        start: int | str,
        end: int | str,
        limit: int = 100,
    ) -> dict[str, Any]:
        filters: list[dict[str, Any]] = [
            {"key": {"key": "service.name"}, "op": "=", "value": service_name}
        ]
        if severity:
            filters.append({"key": {"key": "severity_text"}, "op": "=", "value": severity})
        if query:
            filters.append({"key": {"key": "body"}, "op": "contains", "value": query})
        # SigNoz Cloud /api/v3/query_range 期望 panelType / queryType 在
        # compositeQuery 内嵌套；start/end 是 epoch ms 整数。
        body = {
            "start": to_epoch_ms(start),
            "end": to_epoch_ms(end),
            "step": 60,
            "compositeQuery": {
                "queryType": "builder",
                "panelType": "list",
                "builderQueries": {
                    "A": {
                        "dataSource": "logs",
                        "queryName": "A",
                        "filters": {"items": filters, "op": "AND"},
                        "orderBy": [{"columnName": "timestamp", "order": "desc"}],
                        "pageSize": limit,
                    }
                },
            },
        }
        return await self._query_range(body)

    async def search_traces(
        self, *, service_name: str, start: int | str, end: int | str,
        limit: int = 50,
    ) -> dict[str, Any]:
        # 同 search_logs：panelType / queryType 嵌进 compositeQuery
        body = {
            "start": to_epoch_ms(start),
            "end": to_epoch_ms(end),
            "step": 60,
            "compositeQuery": {
                "queryType": "builder",
                "panelType": "list",
                "builderQueries": {
                    "A": {
                        "dataSource": "traces",
                        "queryName": "A",
                        "filters": {
                            "items": [
                                {"key": {"key": "service.name"}, "op": "=", "value": service_name}
                            ],
                            "op": "AND",
                        },
                        "orderBy": [{"columnName": "timestamp", "order": "desc"}],
                        "pageSize": limit,
                    }
                },
            },
        }
        return await self._query_range(body)

    async def list_services(self, *, start: int | str, end: int | str) -> dict[str, Any]:
        # /api/v1/services 这个老 endpoint 期望 string 格式 epoch ms
        endpoint = f"{self.url}/api/v1/services"
        body = {"start": str(to_epoch_ms(start)), "end": str(to_epoch_ms(end))}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(endpoint, json=body, headers=self._headers())
        if resp.status_code != 200:
            raise RuntimeError(f"SigNoz list_services failed: {resp.status_code} {resp.text[:500]}")
        return resp.json()

    async def get_field_keys(self, *, data_source: str = "logs") -> dict[str, Any]:
        endpoint = f"{self.url}/api/v3/autocomplete/keys"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(
                endpoint, params={"dataSource": data_source}, headers=self._headers(),
            )
        if resp.status_code != 200:
            raise RuntimeError(f"SigNoz get_field_keys failed: {resp.status_code}")
        return resp.json()

    async def get_field_values(
        self, *, data_source: str = "logs", field_key: str,
    ) -> dict[str, Any]:
        endpoint = f"{self.url}/api/v3/autocomplete/values"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(
                endpoint,
                params={"dataSource": data_source, "keyName": field_key},
                headers=self._headers(),
            )
        if resp.status_code != 200:
            raise RuntimeError(f"SigNoz get_field_values failed: {resp.status_code}")
        return resp.json()
