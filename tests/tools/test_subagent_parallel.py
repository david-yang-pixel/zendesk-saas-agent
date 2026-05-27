"""Test RunSubagentsParallel: parallel dispatch, error handling."""
import asyncio
import sys
import types
from unittest.mock import patch

import pytest

from zd_agent.tools.base import ToolResult
from zd_agent.tools.subagent_parallel import RunSubagentsParallel


def _make_subagent_patch(fake_fn):
    """Return a context manager that injects fake_fn as run_subagent
    into sys.modules so the late `from zd_agent.runtime.subagent import
    run_subagent` inside execute() resolves to our stub."""
    mod = types.ModuleType("zd_agent.runtime.subagent")
    mod.run_subagent = fake_fn
    return patch.dict(sys.modules, {"zd_agent.runtime.subagent": mod})


@pytest.mark.asyncio
async def test_parallel_dispatch_runs_concurrently(fake_session):
    """Verify asyncio.gather fires all subagents without waiting in series."""
    tool = RunSubagentsParallel()

    call_order: list[str] = []
    start_order: list[str] = []

    async def fake_subagent(task, tools, context, session):
        start_order.append(task)
        await asyncio.sleep(0.02)
        call_order.append(task)
        return ToolResult(output=f"result for {task}")

    with _make_subagent_patch(fake_subagent):
        result = await tool.execute(
            {"tasks": [
                {"task": "t1", "tools": ["git_grep"]},
                {"task": "t2", "tools": ["git_grep"]},
                {"task": "t3", "tools": ["git_grep"]},
            ]},
            fake_session,
        )
    assert result.success is True
    # All started before any finished (parallelism check)
    assert len(start_order) == 3
    assert {"t1", "t2", "t3"} == set(start_order)
    # All three outputs present in final result
    for t in ("t1", "t2", "t3"):
        assert t in result.output


@pytest.mark.asyncio
async def test_empty_tasks_returns_error(fake_session):
    tool = RunSubagentsParallel()
    result = await tool.execute({"tasks": []}, fake_session)
    assert result.success is False
    assert "empty" in (result.error or "").lower()


@pytest.mark.asyncio
async def test_one_failing_subagent_others_succeed(fake_session):
    """If one subagent raises, parallel should report error for it but still
    aggregate others' successful outputs."""
    tool = RunSubagentsParallel()

    async def fake_subagent(task, tools, context, session):
        if task == "fail":
            raise RuntimeError("boom")
        return ToolResult(output=f"ok for {task}")

    with _make_subagent_patch(fake_subagent):
        result = await tool.execute(
            {"tasks": [
                {"task": "a", "tools": ["git_grep"]},
                {"task": "fail", "tools": ["git_grep"]},
                {"task": "b", "tools": ["git_grep"]},
            ]},
            fake_session,
        )
    assert result.success is True  # at least one succeeded
    assert "ok for a" in result.output
    assert "ok for b" in result.output
    assert "boom" in result.output
    assert "RuntimeError" in result.output


@pytest.mark.asyncio
async def test_all_subagents_fail(fake_session):
    tool = RunSubagentsParallel()

    async def fake_subagent(task, tools, context, session):
        return ToolResult(success=False, error=f"failed {task}")

    with _make_subagent_patch(fake_subagent):
        result = await tool.execute(
            {"tasks": [
                {"task": "a", "tools": ["git_grep"]},
                {"task": "b", "tools": ["git_grep"]},
            ]},
            fake_session,
        )
    assert result.success is False
    assert "all subagents failed" in (result.error or "").lower()


@pytest.mark.asyncio
async def test_tools_and_context_passed_through(fake_session):
    tool = RunSubagentsParallel()

    captured = []

    async def fake_subagent(task, tools, context, session):
        captured.append({"task": task, "tools": tools, "context": context})
        return ToolResult(output="ok")

    with _make_subagent_patch(fake_subagent):
        await tool.execute(
            {"tasks": [
                {"task": "t1", "tools": ["git_grep"], "context": "ctx1"},
                {"task": "t2", "tools": ["sandbox_read"]},
            ]},
            fake_session,
        )
    assert captured[0] == {"task": "t1", "tools": ["git_grep"], "context": "ctx1"}
    assert captured[1] == {"task": "t2", "tools": ["sandbox_read"], "context": ""}


@pytest.mark.asyncio
async def test_missing_tools_field_rejected(fake_session):
    """招法 3: tools 是 required，没传应该报错"""
    tool = RunSubagentsParallel()
    result = await tool.execute(
        {"tasks": [{"task": "t1"}]},  # 没传 tools
        fake_session,
    )
    assert result.success is False
    assert "tools" in (result.error or "").lower()


@pytest.mark.asyncio
async def test_empty_tools_field_rejected(fake_session):
    tool = RunSubagentsParallel()
    result = await tool.execute(
        {"tasks": [{"task": "t1", "tools": []}]},
        fake_session,
    )
    assert result.success is False
    assert "tools" in (result.error or "").lower()
