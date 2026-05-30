"""SigNoz 日志查询工具（基于 sz CLI）测试。"""
import pytest

import zd_agent.tools.signoz_tools as st
from zd_agent.tools.signoz_tools import (
    SignozErrors, SignozTraceback, SignozServices, SignozQuery,
)


@pytest.mark.asyncio
async def test_errors_returns_rows(monkeypatch):
    async def fake_run_sz(*args, **kw):
        assert args[0] == "errors"
        return [{"ts": "2026-05-30 16:13", "sev": "ERROR",
                 "dep": "dify-api-oltp", "id": "x", "body": "boom"}]
    monkeypatch.setattr(st, "run_sz", fake_run_sz)

    tool = SignozErrors()
    r = await tool.execute({"hours": 1, "limit": 3}, session=None)
    assert r.success
    assert "dify-api-oltp" in r.output
    assert "boom" in r.output


@pytest.mark.asyncio
async def test_errors_passes_filters(monkeypatch):
    seen = {}
    async def fake_run_sz(*args, **kw):
        seen["args"] = args
        return []
    monkeypatch.setattr(st, "run_sz", fake_run_sz)

    tool = SignozErrors()
    await tool.execute({"tenant": "abc", "deployment": "dify-app-worker"}, session=None)
    assert "errors" in seen["args"]
    assert "--tenant" in seen["args"] and "abc" in seen["args"]
    assert "--deployment" in seen["args"] and "dify-app-worker" in seen["args"]
    # env 默认 production
    assert "--env" in seen["args"] and "production" in seen["args"]


@pytest.mark.asyncio
async def test_errors_handles_failure(monkeypatch):
    async def fake_run_sz(*args, **kw):
        return None
    monkeypatch.setattr(st, "run_sz", fake_run_sz)

    tool = SignozErrors()
    r = await tool.execute({}, session=None)
    assert not r.success
    assert r.error


@pytest.mark.asyncio
async def test_query_requires_sql(monkeypatch):
    tool = SignozQuery()
    r = await tool.execute({}, session=None)
    assert not r.success


@pytest.mark.asyncio
async def test_traceback_and_services_smoke(monkeypatch):
    async def fake_run_sz(*args, **kw):
        return []
    monkeypatch.setattr(st, "run_sz", fake_run_sz)
    assert (await SignozTraceback().execute({}, session=None)).success
    assert (await SignozServices().execute({}, session=None)).success
