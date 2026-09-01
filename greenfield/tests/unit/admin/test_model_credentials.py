from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest

from casework_admin.model_credentials import (
    FileCredentialStore,
    OpenAIResponsesCredentialProbe,
    parse_credential_file,
)


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
        base_url="https://api.openai.com", client=client
    )

    result = await probe.validate(provider="openai", model="gpt-test", secret=candidate)

    assert result.error_code == "HTTP_401_invalid_api_key"
    assert candidate not in result.model_dump_json()
    assert "Rejected" not in result.model_dump_json()
    await client.aclose()
