"""Test 3 Grafana tools with mocked GrafanaClient."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from zd_agent.tools.grafana_tools import (
    GrafanaListDashboards,
    GrafanaGetDashboard,
    GrafanaQueryPrometheus,
)


@pytest.fixture
def grafana_cfg(monkeypatch):
    """Patch get_config so _client() builds a valid GrafanaClient instance."""
    cfg = MagicMock()
    cfg.grafana.url = "https://g.example.com"
    cfg.grafana.api_key = "k"
    cfg.grafana.prom_datasource_id = 1
    monkeypatch.setattr("zd_agent.tools.grafana_tools.get_config", lambda: cfg)
    return cfg


@pytest.mark.asyncio
async def test_list_dashboards(fake_session, grafana_cfg):
    tool = GrafanaListDashboards()
    fake_result = [{"uid": "u1", "title": "T1", "url": "/d/u1"}]
    with patch("zd_agent.tools.grafana_tools.GrafanaClient") as m:
        m.return_value.list_dashboards = AsyncMock(return_value=fake_result)
        result = await tool.execute({"query": "request"}, fake_session)
    assert result.success is True
    assert "u1" in result.output
    assert "T1" in result.output


@pytest.mark.asyncio
async def test_list_dashboards_empty(fake_session, grafana_cfg):
    tool = GrafanaListDashboards()
    with patch("zd_agent.tools.grafana_tools.GrafanaClient") as m:
        m.return_value.list_dashboards = AsyncMock(return_value=[])
        result = await tool.execute({"query": "none"}, fake_session)
    assert result.success is True
    assert "no dashboards" in result.output


@pytest.mark.asyncio
async def test_query_prometheus(fake_session, grafana_cfg):
    tool = GrafanaQueryPrometheus()
    fake_result = {"status": "success", "data": {"result": [{"metric": {"__name__": "up"}, "values": [[1, "1"]]}]}}
    with patch("zd_agent.tools.grafana_tools.GrafanaClient") as m:
        m.return_value.query_prometheus = AsyncMock(return_value=fake_result)
        result = await tool.execute(
            {"query": "up", "start": "now-1h", "end": "now", "step": "15s"},
            fake_session,
        )
    assert result.success is True
    assert "success" in result.output


@pytest.mark.asyncio
async def test_get_dashboard(fake_session, grafana_cfg):
    tool = GrafanaGetDashboard()
    fake_result = {"dashboard": {"uid": "u1", "panels": []}}
    with patch("zd_agent.tools.grafana_tools.GrafanaClient") as m:
        m.return_value.get_dashboard = AsyncMock(return_value=fake_result)
        result = await tool.execute({"uid": "u1"}, fake_session)
    assert result.success is True
    assert "u1" in result.output


@pytest.mark.asyncio
async def test_tool_returns_error_when_not_configured(fake_session, monkeypatch):
    cfg = MagicMock()
    cfg.grafana.url = ""  # not configured
    cfg.grafana.api_key = ""
    cfg.grafana.prom_datasource_id = 1
    monkeypatch.setattr("zd_agent.tools.grafana_tools.get_config", lambda: cfg)
    tool = GrafanaListDashboards()
    result = await tool.execute({"query": "x"}, fake_session)
    assert result.success is False
    assert "not configured" in (result.error or "").lower()
