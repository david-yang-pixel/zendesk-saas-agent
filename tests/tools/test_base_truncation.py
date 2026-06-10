"""Tests for tools.base 的 max_output_tokens enforce（修法 1）.

之前 max_output_tokens 在各 Tool 类只是装饰未生效，导致 SigNoz 一次返回
146 KB 塞回 agent message_store。修改 ToolRegistry.execute 在每次工具
返回后强制截断。
"""
from __future__ import annotations

import asyncio

import pytest

from zd_agent.tools.base import (
    Tool,
    ToolRegistry,
    ToolResult,
    _enforce_max_output,
)


class _BigOutputTool(Tool):
    name = "big"
    description = "test tool returning big output"
    parameters = {"type": "object", "properties": {}}
    max_output_tokens = 100  # = 400 chars limit

    async def execute(self, args, session):
        return ToolResult(success=True, output="x" * 10000)


class _NoLimitTool(Tool):
    name = "nolimit"
    description = "test tool with no limit"
    parameters = {"type": "object", "properties": {}}

    async def execute(self, args, session):
        return ToolResult(success=True, output="y" * 50000)


class _NormalTool(Tool):
    name = "normal"
    description = "test tool with output under limit"
    parameters = {"type": "object", "properties": {}}
    max_output_tokens = 100

    async def execute(self, args, session):
        return ToolResult(success=True, output="z" * 100)


class TestEnforceMaxOutput:
    def test_truncates_over_limit(self) -> None:
        r = ToolResult(success=True, output="x" * 10000)
        out = _enforce_max_output(r, max_tokens=100, tool_name="big")
        assert len(out.output) < 10000
        assert "truncated" in out.output
        assert "100" in out.output  # max_output_tokens 显示在提示里
        assert out.success is True

    def test_no_limit_passes_through(self) -> None:
        r = ToolResult(success=True, output="y" * 50000)
        out = _enforce_max_output(r, max_tokens=0, tool_name="nolimit")
        assert out.output == "y" * 50000
        assert "truncated" not in out.output

    def test_under_limit_unchanged(self) -> None:
        r = ToolResult(success=True, output="z" * 100)
        out = _enforce_max_output(r, max_tokens=100, tool_name="normal")
        assert out.output == "z" * 100
        assert "truncated" not in out.output

    def test_error_result_passes_through(self) -> None:
        r = ToolResult(success=False, output="", error="bad arg")
        out = _enforce_max_output(r, max_tokens=100, tool_name="x")
        assert out.success is False
        assert out.error == "bad arg"

    def test_empty_output_unchanged(self) -> None:
        r = ToolResult(success=True, output="")
        out = _enforce_max_output(r, max_tokens=100, tool_name="x")
        assert out.output == ""


class TestRegistryEnforces:
    @pytest.mark.asyncio
    async def test_registry_execute_enforces_truncation(self) -> None:
        reg = ToolRegistry()
        reg.register(_BigOutputTool())
        out = await reg.execute("big", "{}", session=None)
        assert "truncated" in out.output
        # 截后总长 < 原长 但保留前 90% (360 chars) + 截断提示
        assert len(out.output) < 10000
        assert len(out.output) > 200  # 至少保留了 90% 的截前内容

    @pytest.mark.asyncio
    async def test_registry_execute_passes_through_no_limit(self) -> None:
        reg = ToolRegistry()
        reg.register(_NoLimitTool())
        out = await reg.execute("nolimit", "{}", session=None)
        assert out.output == "y" * 50000
        assert "truncated" not in out.output

    @pytest.mark.asyncio
    async def test_registry_execute_normal_tool_unchanged(self) -> None:
        reg = ToolRegistry()
        reg.register(_NormalTool())
        out = await reg.execute("normal", "{}", session=None)
        assert out.output == "z" * 100

    @pytest.mark.asyncio
    @pytest.mark.parametrize("args_json", ["[]", "null", '"query"'])
    async def test_registry_execute_rejects_non_object_args(self, args_json: str) -> None:
        reg = ToolRegistry()
        reg.register(_NormalTool())

        out = await reg.execute("normal", args_json, session=None)

        assert out.success is False
        assert out.error == "工具参数必须是 JSON object"
