"""Grafana HTTP API client."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx


@dataclass
class GrafanaClient:
    url: str
    api_key: str
    prom_datasource_id: int
    timeout: float = 30.0

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
        }

    async def query_prometheus(
        self, *, query: str, start: str, end: str, step: str = "15s",
    ) -> dict[str, Any]:
        """Call Prometheus range query via Grafana datasource proxy."""
        endpoint = (
            f"{self.url}/api/datasources/proxy/{self.prom_datasource_id}"
            f"/api/v1/query_range"
        )
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(
                endpoint,
                params={"query": query, "start": start, "end": end, "step": step},
                headers=self._headers(),
            )
        if resp.status_code != 200:
            raise RuntimeError(f"Grafana query failed: {resp.status_code} {resp.text[:500]}")
        return resp.json()

    async def list_dashboards(self, *, query: str = "") -> list[dict[str, Any]]:
        """Search dashboards by title substring."""
        endpoint = f"{self.url}/api/search"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(
                endpoint,
                params={"query": query, "type": "dash-db"},
                headers=self._headers(),
            )
        if resp.status_code != 200:
            raise RuntimeError(f"Grafana list_dashboards failed: {resp.status_code} {resp.text[:500]}")
        return resp.json()

    async def get_dashboard(self, *, uid: str) -> dict[str, Any]:
        """Fetch a dashboard by UID."""
        endpoint = f"{self.url}/api/dashboards/uid/{uid}"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(endpoint, headers=self._headers())
        if resp.status_code != 200:
            raise RuntimeError(f"Grafana get_dashboard failed: {resp.status_code} {resp.text[:500]}")
        return resp.json()
