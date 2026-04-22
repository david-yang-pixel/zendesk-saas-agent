"""Test SigNoz HTTP client."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from zd_agent.integrations.signoz import SignozClient


def _mock_response(status: int, json_body):
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = json_body
    resp.text = str(json_body)
    return resp


class _AsyncClientCtx:
    def __init__(self, mock):
        self.mock = mock
    async def __aenter__(self):
        return self.mock
    async def __aexit__(self, *a):
        return False


@pytest.mark.asyncio
async def test_signoz_search_logs():
    client = SignozClient(url="https://signoz.example.com", api_key="k")
    fake_json = {"data": {"result": [{"list": [{"body": "error X", "severity_text": "ERROR"}]}]}}
    mock_http = MagicMock()
    mock_http.post = AsyncMock(return_value=_mock_response(200, fake_json))
    with patch("httpx.AsyncClient", return_value=_AsyncClientCtx(mock_http)):
        result = await client.search_logs(
            service_name="api", severity="ERROR", query="500",
            start="now-1h", end="now", limit=50,
        )
    assert "data" in result


@pytest.mark.asyncio
async def test_signoz_search_traces():
    client = SignozClient(url="https://s.example.com", api_key="k")
    fake_json = {"data": {"result": []}}
    mock_http = MagicMock()
    mock_http.post = AsyncMock(return_value=_mock_response(200, fake_json))
    with patch("httpx.AsyncClient", return_value=_AsyncClientCtx(mock_http)):
        result = await client.search_traces(
            service_name="api", start="now-1h", end="now", limit=10,
        )
    assert "data" in result


@pytest.mark.asyncio
async def test_signoz_list_services():
    client = SignozClient(url="https://s.example.com", api_key="k")
    fake_json = {"data": [{"serviceName": "api"}, {"serviceName": "worker"}]}
    mock_http = MagicMock()
    mock_http.post = AsyncMock(return_value=_mock_response(200, fake_json))
    with patch("httpx.AsyncClient", return_value=_AsyncClientCtx(mock_http)):
        result = await client.list_services(start="now-1h", end="now")
    assert len(result["data"]) == 2


@pytest.mark.asyncio
async def test_signoz_get_field_keys():
    client = SignozClient(url="https://s.example.com", api_key="k")
    fake_json = {"data": [{"key": "service.name"}, {"key": "severity_text"}]}
    mock_http = MagicMock()
    mock_http.get = AsyncMock(return_value=_mock_response(200, fake_json))
    with patch("httpx.AsyncClient", return_value=_AsyncClientCtx(mock_http)):
        result = await client.get_field_keys(data_source="logs")
    assert "data" in result


@pytest.mark.asyncio
async def test_signoz_get_field_values():
    client = SignozClient(url="https://s.example.com", api_key="k")
    fake_json = {"data": ["ERROR", "WARN", "INFO"]}
    mock_http = MagicMock()
    mock_http.get = AsyncMock(return_value=_mock_response(200, fake_json))
    with patch("httpx.AsyncClient", return_value=_AsyncClientCtx(mock_http)):
        result = await client.get_field_values(data_source="logs", field_key="severity_text")
    assert "data" in result


@pytest.mark.asyncio
async def test_signoz_error_on_non_200():
    client = SignozClient(url="https://s.example.com", api_key="bad")
    mock_http = MagicMock()
    mock_http.post = AsyncMock(return_value=_mock_response(401, {"error": "unauthorized"}))
    with patch("httpx.AsyncClient", return_value=_AsyncClientCtx(mock_http)):
        with pytest.raises(RuntimeError, match="401"):
            await client.list_services(start="now-1h", end="now")
