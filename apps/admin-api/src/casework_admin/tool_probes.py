"""Read-only connection probes for tools shown in the Admin registry.

The probe boundary deliberately accepts only health URLs.  It never sends a
ticket, tool arguments, credentials, or a request body, and it turns transport
failures into a small typed status that is safe to render in the console.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Mapping
from typing import Protocol
from urllib.parse import urlsplit

import httpx

from casework_admin.read_models import ConnectionProbeResponse


class ProbeHttpResponse(Protocol):
    status_code: int


class AsyncProbeHttpClient(Protocol):
    async def get(
        self, url: str, *, timeout: float  # noqa: ASYNC109 - outbound I/O bound
    ) -> ProbeHttpResponse: ...


class HttpToolProbe:
    """Probe configured tool health endpoints using bounded HTTP GET requests."""

    def __init__(
        self,
        endpoints: Mapping[str, str],
        *,
        client: AsyncProbeHttpClient | None = None,
        timeout_seconds: float = 3.0,
    ) -> None:
        if not 0.01 <= timeout_seconds <= 30.0:
            raise ValueError("tool probe timeout must be between 0.01 and 30 seconds")
        self._endpoints = {
            self._validate_tool_id(tool_id): self._validate_endpoint(endpoint)
            for tool_id, endpoint in endpoints.items()
        }
        self._timeout_seconds = timeout_seconds
        self._client = client or httpx.AsyncClient(follow_redirects=False)
        self._owns_client = client is None

    @staticmethod
    def _validate_tool_id(tool_id: str) -> str:
        normalized = tool_id.strip()
        if not normalized:
            raise ValueError("tool probe id must not be empty")
        return normalized

    @staticmethod
    def _validate_endpoint(endpoint: str) -> str:
        normalized = endpoint.strip()
        parsed = urlsplit(normalized)
        if parsed.scheme.casefold() != "https":
            raise ValueError("tool probe endpoint must use HTTPS")
        if not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("tool probe endpoint must be credential-free")
        if parsed.query or parsed.fragment:
            raise ValueError("tool probe endpoint must be credential-free")
        return normalized

    async def probe(self, tool_id: str) -> ConnectionProbeResponse:
        endpoint = self._endpoints.get(tool_id)
        if endpoint is None:
            return ConnectionProbeResponse(
                tool_id=tool_id,
                status="untested",
                latency_ms=None,
                error_code="not_configured",
                detail="No probe endpoint is configured for this tool",
            )

        started = time.perf_counter()
        try:
            response = await asyncio.wait_for(
                self._client.get(endpoint, timeout=self._timeout_seconds),
                timeout=self._timeout_seconds,
            )
        except (TimeoutError, httpx.TimeoutException):
            return ConnectionProbeResponse(
                tool_id=tool_id,
                status="degraded",
                latency_ms=None,
                error_code="timeout",
                detail="Tool connection probe timed out",
            )
        except Exception:
            # Do not expose exception text: client errors can contain URLs,
            # headers, or provider-specific secret material.
            return ConnectionProbeResponse(
                tool_id=tool_id,
                status="degraded",
                latency_ms=None,
                error_code="unavailable",
                detail="Tool endpoint is unavailable",
            )

        latency_ms = max(0, int((time.perf_counter() - started) * 1000))
        if 200 <= response.status_code < 300:
            return ConnectionProbeResponse(
                tool_id=tool_id,
                status="connected",
                latency_ms=latency_ms,
            )
        return ConnectionProbeResponse(
            tool_id=tool_id,
            status="degraded",
            latency_ms=latency_ms,
            error_code="unavailable",
            detail="Tool endpoint is unavailable",
        )

    async def aclose(self) -> None:
        if self._owns_client and isinstance(self._client, httpx.AsyncClient):
            await self._client.aclose()


__all__ = ["AsyncProbeHttpClient", "HttpToolProbe", "ProbeHttpResponse"]
