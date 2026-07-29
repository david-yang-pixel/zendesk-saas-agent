"""SigNoz 日志查询工具（基于远程 sz MCP）测试。"""
import pytest

import zd_agent.tools.signoz_tools as st
from zd_agent.tools.signoz_tools import (
    SIGNOZ_STRUCTURED_SPECS,
    SignozErrors,
    SignozQuery,
    SignozServices,
    SignozTraceback,
    structured_signoz_tools,
)


@pytest.mark.asyncio
async def test_errors_returns_rows(monkeypatch):
    async def fake_run_signoz_sz(*args, **kw):
        assert args[0] == "errors"
        return [{"ts": "2026-05-30 16:13", "sev": "ERROR",
                 "dep": "dify-api-oltp", "id": "x", "body": "boom"}]
    monkeypatch.setattr(st, "run_signoz_sz", fake_run_signoz_sz)

    tool = SignozErrors()
    r = await tool.execute({"hours": 1, "limit": 3}, session=None)
    assert r.success
    assert "dify-api-oltp" in r.output
    assert "boom" in r.output


@pytest.mark.asyncio
async def test_errors_passes_filters(monkeypatch):
    seen = {}
    async def fake_run_signoz_sz(*args, **kw):
        seen["args"] = args
        return []
    monkeypatch.setattr(st, "run_signoz_sz", fake_run_signoz_sz)

    tool = SignozErrors()
    await tool.execute({"tenant": "abc", "deployment": "dify-app-worker"}, session=None)
    assert "errors" in seen["args"]
    assert "--tenant" in seen["args"] and "abc" in seen["args"]
    assert "--deployment" in seen["args"] and "dify-app-worker" in seen["args"]
    # env 默认 production
    assert "--env" in seen["args"] and "production" in seen["args"]


@pytest.mark.asyncio
async def test_errors_handles_failure(monkeypatch):
    async def fake_run_signoz_sz(*args, **kw):
        return None
    monkeypatch.setattr(st, "run_signoz_sz", fake_run_signoz_sz)

    tool = SignozErrors()
    r = await tool.execute({}, session=None)
    assert not r.success
    assert r.error


@pytest.mark.asyncio
async def test_errors_rejects_invalid_numeric_args_before_call(monkeypatch):
    async def fake_run_signoz_sz(*args, **kw):
        raise AssertionError("run_signoz_sz should not be called")
    monkeypatch.setattr(st, "run_signoz_sz", fake_run_signoz_sz)

    r = await SignozErrors().execute({"hours": "soon"}, session=None)
    assert not r.success
    assert "hours" in (r.error or "")

    r = await SignozErrors().execute({"limit": "many"}, session=None)
    assert not r.success
    assert "limit" in (r.error or "")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool", "args", "expected"),
    [
        (SignozErrors(), {"env": ["production"]}, "env must be a string"),
        (SignozErrors(), {"tenant": {"id": "tenant-1"}}, "tenant must be a string"),
        (SignozTraceback(), {"id": ["log-1"]}, "id must be a string"),
        (SignozTraceback(), {"grep": {"q": "boom"}}, "grep must be a string"),
        (SignozServices(), {"env": ["production"]}, "env must be a string"),
        (SignozQuery(), {"sql": ["SELECT 1"]}, "sql must be a string"),
    ],
)
async def test_rejects_invalid_string_args_before_call(
    monkeypatch,
    tool,
    args,
    expected,
):
    async def fake_run_signoz_sz(*_args, **_kw):
        raise AssertionError("run_signoz_sz should not be called")
    monkeypatch.setattr(st, "run_signoz_sz", fake_run_signoz_sz)

    r = await tool.execute(args, session=None)

    assert not r.success
    assert r.error == expected


@pytest.mark.asyncio
async def test_query_rejects_invalid_hours_before_call(monkeypatch):
    async def fake_run_signoz_sz(*args, **kw):
        raise AssertionError("run_signoz_sz should not be called")
    monkeypatch.setattr(st, "run_signoz_sz", fake_run_signoz_sz)

    r = await SignozQuery().execute({"sql": "SELECT 1", "hours": "later"}, session=None)
    assert not r.success
    assert "hours" in (r.error or "")

    r = await SignozQuery().execute({"sql": "SELECT 1", "hours": "nan"}, session=None)
    assert not r.success
    assert "hours" in (r.error or "")


@pytest.mark.asyncio
async def test_query_requires_sql(monkeypatch):
    tool = SignozQuery()
    r = await tool.execute({}, session=None)
    assert not r.success


@pytest.mark.asyncio
async def test_traceback_and_services_smoke(monkeypatch):
    async def fake_run_signoz_sz(*args, **kw):
        return []
    monkeypatch.setattr(st, "run_signoz_sz", fake_run_signoz_sz)
    assert (await SignozTraceback().execute({}, session=None)).success
    assert (await SignozServices().execute({}, session=None)).success


def test_structured_signoz_tool_specs_match_remote_surface():
    assert {spec["name"] for spec in SIGNOZ_STRUCTURED_SPECS} == {
        "signoz_list_services",
        "signoz_get_trace_details",
        "signoz_list_metrics",
        "signoz_get_field_keys",
        "signoz_get_field_values",
        "signoz_search_logs",
        "signoz_aggregate_logs",
        "signoz_search_traces",
        "signoz_aggregate_traces",
        "signoz_query_metrics",
    }


@pytest.mark.asyncio
async def test_structured_signoz_tool_forwards_arguments(monkeypatch):
    seen = {}

    async def fake_call_signoz_tool(name, arguments):
        seen["name"] = name
        seen["arguments"] = arguments
        return {
            "success": True,
            "data": [{"deployment": "dify-app-worker", "count": 12}],
        }

    monkeypatch.setattr(st, "call_signoz_tool", fake_call_signoz_tool)
    tool = next(
        tool for tool in structured_signoz_tools()
        if tool.name == "signoz_aggregate_logs"
    )

    result = await tool.execute(
        {
            "aggregation": "count",
            "groupBy": "resource.k8s.deployment.name",
            "timeRange": "1h",
        },
        session=None,
    )

    assert result.success is True
    assert seen == {
        "name": "signoz_aggregate_logs",
        "arguments": {
            "aggregation": "count",
            "groupBy": "resource.k8s.deployment.name",
            "timeRange": "1h",
        },
    }
    assert "dify-app-worker" in result.output


@pytest.mark.asyncio
async def test_structured_signoz_tool_preserves_remote_error(monkeypatch):
    async def fake_call_signoz_tool(name, arguments):
        return {
            "success": False,
            "code": "BAD_ARGUMENT",
            "error": "unknown field",
        }

    monkeypatch.setattr(st, "call_signoz_tool", fake_call_signoz_tool)
    tool = next(
        tool for tool in structured_signoz_tools()
        if tool.name == "signoz_search_logs"
    )

    result = await tool.execute({"timeRange": "1h"}, session=None)

    assert result.success is False
    assert "BAD_ARGUMENT" in (result.error or "")
    assert "unknown field" in (result.error or "")
    assert result.metadata["mcp_code"] == "BAD_ARGUMENT"
