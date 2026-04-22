"""Test 5 SigNoz tools with mocked SignozClient."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from zd_agent.tools.signoz_tools import (
    SignozSearchLogs,
    SignozSearchTraces,
    SignozListServices,
    SignozGetFieldKeys,
    SignozGetFieldValues,
)


@pytest.fixture
def signoz_cfg(monkeypatch):
    cfg = MagicMock()
    cfg.signoz.url = "https://s.example.com"
    cfg.signoz.api_key = "k"
    monkeypatch.setattr("zd_agent.tools.signoz_tools.get_config", lambda: cfg)
    return cfg


@pytest.mark.asyncio
async def test_search_logs(fake_session, signoz_cfg):
    tool = SignozSearchLogs()
    with patch("zd_agent.tools.signoz_tools.SignozClient") as m:
        m.return_value.search_logs = AsyncMock(return_value={"data": {"result": []}})
        r = await tool.execute(
            {"service_name": "api", "start": "now-1h", "end": "now"},
            fake_session,
        )
    assert r.success is True


@pytest.mark.asyncio
async def test_search_traces(fake_session, signoz_cfg):
    tool = SignozSearchTraces()
    with patch("zd_agent.tools.signoz_tools.SignozClient") as m:
        m.return_value.search_traces = AsyncMock(return_value={"data": {}})
        r = await tool.execute(
            {"service_name": "api", "start": "now-1h", "end": "now"},
            fake_session,
        )
    assert r.success is True


@pytest.mark.asyncio
async def test_list_services(fake_session, signoz_cfg):
    tool = SignozListServices()
    with patch("zd_agent.tools.signoz_tools.SignozClient") as m:
        m.return_value.list_services = AsyncMock(return_value={"data": [{"serviceName": "api"}]})
        r = await tool.execute({"start": "now-1h", "end": "now"}, fake_session)
    assert r.success is True
    assert "api" in r.output


@pytest.mark.asyncio
async def test_get_field_keys(fake_session, signoz_cfg):
    tool = SignozGetFieldKeys()
    with patch("zd_agent.tools.signoz_tools.SignozClient") as m:
        m.return_value.get_field_keys = AsyncMock(return_value={"data": []})
        r = await tool.execute({}, fake_session)
    assert r.success is True


@pytest.mark.asyncio
async def test_get_field_values(fake_session, signoz_cfg):
    tool = SignozGetFieldValues()
    with patch("zd_agent.tools.signoz_tools.SignozClient") as m:
        m.return_value.get_field_values = AsyncMock(return_value={"data": []})
        r = await tool.execute({"field_key": "severity_text"}, fake_session)
    assert r.success is True


@pytest.mark.asyncio
async def test_error_when_not_configured(fake_session, monkeypatch):
    cfg = MagicMock()
    cfg.signoz.url = ""
    cfg.signoz.api_key = ""
    monkeypatch.setattr("zd_agent.tools.signoz_tools.get_config", lambda: cfg)
    tool = SignozSearchLogs()
    r = await tool.execute(
        {"service_name": "api", "start": "now-1h", "end": "now"},
        fake_session,
    )
    assert r.success is False
    assert "not configured" in (r.error or "").lower()
