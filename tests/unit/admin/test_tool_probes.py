from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest
from pydantic import ValidationError

from casework_admin.read_models import ConnectionProbeResponse
from casework_admin.tool_probes import HttpToolProbe


@dataclass
class _Response:
    status_code: int


class _FakeClient:
    def __init__(
        self,
        response: _Response | None = None,
        *,
        error: Exception | None = None,
    ) -> None:
        self.response = response
        self.error = error
        self.calls: list[tuple[str, float]] = []

    async def get(
        self, url: str, *, timeout: float  # noqa: ASYNC109 - timeout contract
    ) -> _Response:
        self.calls.append((url, timeout))
        if self.error is not None:
            raise self.error
        assert self.response is not None
        return self.response


class _HangingClient:
    async def get(
        self, url: str, *, timeout: float  # noqa: ASYNC109 - timeout contract
    ) -> _Response:
        await asyncio.sleep(timeout * 4)
        return _Response(status_code=200)


@pytest.mark.asyncio
async def test_http_tool_probe_is_typed_read_only_and_reports_success_latency() -> None:
    client = _FakeClient(_Response(status_code=204))
    probe = HttpToolProbe(
        {"zendesk": "https://tool.example/health"},
        client=client,
        timeout_seconds=2.5,
    )

    result = await probe.probe("zendesk")

    assert result.tool_id == "zendesk"
    assert result.status == "connected"
    assert result.error_code is None
    assert result.detail is None
    assert result.latency_ms is not None
    assert client.calls == [("https://tool.example/health", 2.5)]


@pytest.mark.asyncio
async def test_http_tool_probe_maps_timeout_without_leaking_exception_text() -> None:
    secret = "Bearer top-secret-token"
    probe = HttpToolProbe(
        {"zendesk": "https://tool.example/health"},
        client=_HangingClient(),
        timeout_seconds=0.01,
    )

    result = await probe.probe("zendesk")

    assert result.status == "degraded"
    assert result.error_code == "timeout"
    assert result.detail == "Tool connection probe timed out"
    assert secret not in result.model_dump_json()


@pytest.mark.asyncio
async def test_http_tool_probe_maps_http_failure_without_response_body() -> None:
    client = _FakeClient(_Response(status_code=503))
    probe = HttpToolProbe(
        {"zendesk": "https://tool.example/health"},
        client=client,
    )

    result = await probe.probe("zendesk")

    assert result.status == "degraded"
    assert result.error_code == "unavailable"
    assert result.detail == "Tool endpoint is unavailable"


@pytest.mark.asyncio
async def test_http_tool_probe_hides_transport_exception_text() -> None:
    secret = "provider=secret-value"
    probe = HttpToolProbe(
        {"zendesk": "https://tool.example/health"},
        client=_FakeClient(error=RuntimeError(secret)),
    )

    result = await probe.probe("zendesk")

    assert result.status == "degraded"
    assert result.error_code == "unavailable"
    assert result.detail == "Tool endpoint is unavailable"
    assert secret not in result.model_dump_json()


@pytest.mark.asyncio
async def test_http_tool_probe_fails_closed_for_unknown_tool() -> None:
    probe = HttpToolProbe(
        {"zendesk": "https://tool.example/health"}, client=_FakeClient()
    )

    result = await probe.probe("sql")

    assert result.status == "untested"
    assert result.error_code == "not_configured"
    assert result.detail == "No probe endpoint is configured for this tool"


def test_http_tool_probe_rejects_non_https_and_credential_bearing_urls() -> None:
    with pytest.raises(ValueError, match="HTTPS"):
        HttpToolProbe({"zendesk": "http://tool.example/health"})
    with pytest.raises(ValueError, match="credential-free"):
        HttpToolProbe({"zendesk": "https://user:secret@tool.example/health"})
    with pytest.raises(ValueError, match="credential-free"):
        HttpToolProbe({"zendesk": "https://tool.example/health?token=secret"})


def test_probe_response_rejects_untyped_diagnostics_that_could_leak_provider_data(
) -> None:
    with pytest.raises(ValidationError):
        ConnectionProbeResponse(
            tool_id="zendesk",
            status="degraded",
            detail="provider response: Bearer top-secret-token",  # type: ignore[arg-type]
        )
