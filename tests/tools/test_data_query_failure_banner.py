"""Tests: ToolRegistry 给 database_query 类工具失败附防臆造横幅（堵 #2709 幻觉）。"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from zd_agent.tools.base import Tool, ToolRegistry, ToolResult


def _make_session():
    s = MagicMock()
    s.ticket_id = 12345
    return s


class _FailingDataTool(Tool):
    name = "failing_data_tool"
    description = "always fails"
    parameters = {"type": "object", "properties": {}}
    group = "database_query"

    async def execute(self, args, session):
        return ToolResult(success=False, error="boom: connection refused")


class _SucceedingDataTool(Tool):
    name = "ok_data_tool"
    description = "ok"
    parameters = {"type": "object", "properties": {}}
    group = "database_query"

    async def execute(self, args, session):
        return ToolResult(output="rows: 3")


class _FailingNonDataTool(Tool):
    name = "failing_other_tool"
    description = "non-data tool that fails"
    parameters = {"type": "object", "properties": {}}
    group = "default"

    async def execute(self, args, session):
        return ToolResult(success=False, error="some other failure")


@pytest.mark.asyncio
async def test_data_query_failure_gets_banner():
    reg = ToolRegistry()
    reg.register(_FailingDataTool())
    r = await reg.execute("failing_data_tool", "{}", _make_session())
    assert r.success is False
    # 原始 error 保留
    assert "boom: connection refused" in (r.error or "")
    # 防臆造横幅注入
    assert "数据未获取" in (r.error or "")
    assert "needs_human" in (r.error or "")


@pytest.mark.asyncio
async def test_data_query_success_no_banner():
    reg = ToolRegistry()
    reg.register(_SucceedingDataTool())
    r = await reg.execute("ok_data_tool", "{}", _make_session())
    assert r.success is True
    assert "数据未获取" not in (r.output or "")


@pytest.mark.asyncio
async def test_non_data_tool_failure_no_banner():
    """非 database_query 工具失败不加横幅（横幅只针对数据查询类）"""
    reg = ToolRegistry()
    reg.register(_FailingNonDataTool())
    r = await reg.execute("failing_other_tool", "{}", _make_session())
    assert r.success is False
    assert "some other failure" in (r.error or "")
    assert "数据未获取" not in (r.error or "")
