"""Tests for the DB Gateway HTTP client."""
from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest

from zd_agent.integrations import db_gateway


def _patch_async_client(
    monkeypatch: pytest.MonkeyPatch,
    handler: Callable[[httpx.Request], httpx.Response],
) -> list[httpx.Request]:
    requests: list[httpx.Request] = []
    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(lambda request: (requests.append(request), handler(request))[1])

    def _client(**kwargs: object) -> httpx.AsyncClient:
        return real_client(transport=transport, **kwargs)

    monkeypatch.setattr(db_gateway.httpx, "AsyncClient", _client)
    return requests


@pytest.mark.asyncio
async def test_query_db_gateway_posts_to_query_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    requests = _patch_async_client(
        monkeypatch,
        lambda _request: httpx.Response(
            200,
            json={
                "success": True,
                "csv": "ok\n1\n",
                "row_count": 1,
                "elapsed_ms": 12,
                "truncated": False,
            },
        ),
    )

    result = await db_gateway.query_db_gateway(
        url="https://db.example.com/db-gateway",
        api_key="secret",
        database="prod",
        sql="select 1",
        ticket_id="2861",
        timeout=30,
    )

    assert result.success is True
    assert result.csv == "ok\n1\n"
    assert result.row_count == 1
    assert result.elapsed_ms == 12
    assert str(requests[0].url) == "https://db.example.com/db-gateway/query"
    assert requests[0].headers["x-api-key"] == "secret"


@pytest.mark.asyncio
async def test_query_db_gateway_returns_structured_error_on_transport_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _post(*_args: object, **_kwargs: object) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    class FailingClient:
        def __init__(self, **_kwargs: object) -> None:
            pass

        async def __aenter__(self) -> "FailingClient":
            return self

        async def __aexit__(self, *_args: object) -> None:
            pass

        post = _post

    monkeypatch.setattr(db_gateway.httpx, "AsyncClient", FailingClient)

    result = await db_gateway.query_db_gateway(
        url="https://db.example.com",
        api_key="secret",
        database="prod",
        sql="select 1",
        ticket_id="2861",
        timeout=30,
    )

    assert result.success is False
    assert result.code == "REQUEST_ERROR"
    assert "connection refused" in (result.error or "")


@pytest.mark.asyncio
async def test_query_db_gateway_returns_structured_error_on_invalid_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_async_client(monkeypatch, lambda _request: httpx.Response(200, text="not json"))

    result = await db_gateway.query_db_gateway(
        url="https://db.example.com",
        api_key="secret",
        database="prod",
        sql="select 1",
        ticket_id="2861",
        timeout=30,
    )

    assert result.success is False
    assert result.code == "BAD_JSON"
    assert "Invalid DB gateway JSON response" in (result.error or "")


@pytest.mark.asyncio
async def test_query_db_gateway_returns_structured_error_on_non_object_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_async_client(monkeypatch, lambda _request: httpx.Response(200, json=["unexpected"]))

    result = await db_gateway.query_db_gateway(
        url="https://db.example.com",
        api_key="secret",
        database="prod",
        sql="select 1",
        ticket_id="2861",
        timeout=30,
    )

    assert result.success is False
    assert result.code == "BAD_RESPONSE"
    assert "Expected DB gateway JSON object" in (result.error or "")
