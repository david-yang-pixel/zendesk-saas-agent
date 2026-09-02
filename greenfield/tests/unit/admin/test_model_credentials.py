from __future__ import annotations

import asyncio
import socket
from pathlib import Path

import httpx
import pytest

from casework_admin.model_credentials import (
    FileCredentialStore,
    ModelCredentialValidationError,
    OpenAIResponsesCredentialProbe,
    parse_credential_file,
)


async def _allow_public_endpoint(_base_url: str) -> None:
    return None


@pytest.mark.asyncio
async def test_file_store_serializes_concurrent_updates(tmp_path: Path) -> None:
    path = tmp_path / "credentials.env"
    store = FileCredentialStore(path)

    await asyncio.gather(
        store.replace("secret/root", "root-secret-value"),
        store.replace("secret/child", "child-secret-value"),
    )

    assert parse_credential_file(path) == {
        "CASEWORK_CREDENTIAL_SECRET_CHILD": "child-secret-value",
        "CASEWORK_CREDENTIAL_SECRET_ROOT": "root-secret-value",
    }


@pytest.mark.asyncio
async def test_openai_probe_returns_only_sanitized_provider_error_code() -> None:
    candidate = "candidate-must-not-be-returned"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == f"Bearer {candidate}"
        return httpx.Response(
            401,
            json={
                "error": {
                    "code": "invalid_api_key",
                    "message": f"Rejected {candidate}",
                }
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    probe = OpenAIResponsesCredentialProbe(
        base_url="https://api.openai.com",
        client=client,
        endpoint_guard=_allow_public_endpoint,
    )

    result = await probe.validate(provider="openai", model="gpt-test", secret=candidate)

    assert result.error_code == "HTTP_401_invalid_api_key"
    assert candidate not in result.model_dump_json()
    assert "Rejected" not in result.model_dump_json()
    await client.aclose()


@pytest.mark.asyncio
async def test_model_discovery_normalizes_base_url_and_bounds_model_ids() -> None:
    candidate = "candidate-model-list-secret"

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://models.example.com/v1/models"
        assert request.headers["authorization"] == f"Bearer {candidate}"
        return httpx.Response(
            200,
            json={"data": [{"id": "gpt-z"}, {"id": "gpt-a"}, {"id": "gpt-a"}]},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    probe = OpenAIResponsesCredentialProbe(
        client=client, endpoint_guard=_allow_public_endpoint
    )

    catalog = await probe.discover(
        provider="openai",
        base_url="https://models.example.com/v1/",
        secret=candidate,
    )

    assert catalog.base_url == "https://models.example.com"
    assert catalog.models == ("gpt-a", "gpt-z")
    assert candidate not in catalog.model_dump_json()
    await client.aclose()


@pytest.mark.asyncio
async def test_model_discovery_sanitizes_provider_failure() -> None:
    candidate = "candidate-model-list-secret"

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401,
            json={"error": {"code": "invalid_api_key", "message": candidate}},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    probe = OpenAIResponsesCredentialProbe(
        client=client, endpoint_guard=_allow_public_endpoint
    )

    with pytest.raises(ModelCredentialValidationError) as captured:
        await probe.discover(
            provider="openai",
            base_url="https://models.example.com",
            secret=candidate,
        )

    assert captured.value.result.error_code == "HTTP_401_invalid_api_key"
    assert candidate not in str(captured.value)
    await client.aclose()


@pytest.mark.asyncio
async def test_model_discovery_rejects_hostname_resolving_to_private_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def private_dns(
        _host: str,
        port: int,
        *,
        family: int,
        type: int,
    ) -> list[tuple[int, int, int, str, tuple[str, int]]]:
        assert family == socket.AF_UNSPEC
        assert type == socket.SOCK_STREAM
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.8", port))]

    monkeypatch.setattr(socket, "getaddrinfo", private_dns)
    probe = OpenAIResponsesCredentialProbe(
        client=httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _request: pytest.fail("unsafe endpoint must not be requested")
            )
        )
    )

    with pytest.raises(ModelCredentialValidationError) as captured:
        await probe.discover(
            provider="openai",
            base_url="https://models.example.com",
            secret="candidate-model-list-secret",
        )

    assert captured.value.result.error_code == "unsafe_endpoint"
    await probe.aclose()
