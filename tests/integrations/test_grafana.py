"""Test Grafana HTTP client."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from zd_agent.integrations.grafana import GrafanaClient


def _mock_response(status: int, json_body):
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = json_body
    resp.text = str(json_body)
    return resp


class _AsyncClientCtx:
    """Minimal async context manager that returns a MagicMock for .get/.post."""
    def __init__(self, mock):
        self.mock = mock
    async def __aenter__(self):
        return self.mock
    async def __aexit__(self, *a):
        return False


@pytest.mark.asyncio
async def test_grafana_query_prometheus():
    client = GrafanaClient(
        url="https://grafana.example.com",
        api_key="key",
        prom_datasource_id=1,
    )
    fake_json = {
        "status": "success",
        "data": {
            "resultType": "matrix",
            "result": [{"metric": {"__name__": "up"}, "values": [[1700000000, "1"]]}],
        },
    }
    mock_http = MagicMock()
    mock_http.get = AsyncMock(return_value=_mock_response(200, fake_json))
    with patch("httpx.AsyncClient", return_value=_AsyncClientCtx(mock_http)):
        result = await client.query_prometheus(
            query="up",
            start="2026-04-22T00:00:00Z",
            end="2026-04-22T01:00:00Z",
            step="15s",
        )
    assert result["status"] == "success"
    assert len(result["data"]["result"]) == 1


@pytest.mark.asyncio
async def test_grafana_list_dashboards():
    client = GrafanaClient(url="https://g.example.com", api_key="k", prom_datasource_id=1)
    fake_json = [
        {"uid": "abc", "title": "Request Success Rate", "url": "/d/abc"},
    ]
    mock_http = MagicMock()
    mock_http.get = AsyncMock(return_value=_mock_response(200, fake_json))
    with patch("httpx.AsyncClient", return_value=_AsyncClientCtx(mock_http)):
        result = await client.list_dashboards(query="request")
    assert len(result) == 1
    assert result[0]["uid"] == "abc"


@pytest.mark.asyncio
async def test_grafana_auth_failure_raises():
    client = GrafanaClient(url="https://g.example.com", api_key="k", prom_datasource_id=1)
    mock_http = MagicMock()
    mock_http.get = AsyncMock(return_value=_mock_response(401, {"message": "unauthorized"}))
    with patch("httpx.AsyncClient", return_value=_AsyncClientCtx(mock_http)):
        with pytest.raises(RuntimeError, match="401"):
            await client.list_dashboards(query="x")


@pytest.mark.asyncio
async def test_grafana_get_dashboard():
    client = GrafanaClient(url="https://g.example.com", api_key="k", prom_datasource_id=1)
    fake_json = {"dashboard": {"uid": "abc", "panels": []}}
    mock_http = MagicMock()
    mock_http.get = AsyncMock(return_value=_mock_response(200, fake_json))
    with patch("httpx.AsyncClient", return_value=_AsyncClientCtx(mock_http)):
        result = await client.get_dashboard(uid="abc")
    assert result["dashboard"]["uid"] == "abc"
