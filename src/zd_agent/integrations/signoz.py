"""SigNoz HTTP API client (SigNoz Cloud v3 query API)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

import httpx


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
        start: str,
        end: str,
        limit: int = 100,
    ) -> dict[str, Any]:
        filters: list[dict[str, Any]] = [
            {"key": {"key": "service.name"}, "op": "=", "value": service_name}
        ]
        if severity:
            filters.append({"key": {"key": "severity_text"}, "op": "=", "value": severity})
        if query:
            filters.append({"key": {"key": "body"}, "op": "contains", "value": query})
        body = {
            "start": start,
            "end": end,
            "step": 60,
            "dataSource": "logs",
            "queryType": "builder",
            "panelType": "list",
            "compositeQuery": {
                "builderQueries": {
                    "A": {
                        "dataSource": "logs",
                        "queryName": "A",
                        "filters": {"items": filters, "op": "AND"},
                        "orderBy": [{"columnName": "timestamp", "order": "desc"}],
                        "pageSize": limit,
                    }
                }
            },
        }
        return await self._query_range(body)

    async def search_traces(
        self, *, service_name: str, start: str, end: str, limit: int = 50,
    ) -> dict[str, Any]:
        body = {
            "start": start,
            "end": end,
            "step": 60,
            "dataSource": "traces",
            "queryType": "builder",
            "panelType": "list",
            "compositeQuery": {
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
                }
            },
        }
        return await self._query_range(body)

    async def list_services(self, *, start: str, end: str) -> dict[str, Any]:
        endpoint = f"{self.url}/api/v1/services"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(
                endpoint, json={"start": start, "end": end}, headers=self._headers(),
            )
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
