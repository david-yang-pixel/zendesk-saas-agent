import asyncio

import pytest

from zd_agent.runtime import log_reporter
from zd_agent.runtime.events import LoopFinished, ToolExecuted
from zd_agent.runtime.log_reporter import LogReporter


@pytest.mark.asyncio
async def test_report_run_start_marks_saas_source(monkeypatch):
    monkeypatch.setenv("HC_CLIENT_URL", "https://hc.example.com")
    monkeypatch.setenv("HC_CLIENT_API_KEY", "agent-key")
    captured = {}

    async def fake_post(path, data):
        captured["path"] = path
        captured["data"] = data
        return {"id": 123}

    reporter = LogReporter()
    monkeypatch.setattr(reporter, "_post", fake_post)

    run_id = await reporter.report_run_start(
        ticket_id=2849,
        session_id="session-1",
        trigger_type="webhook",
        model="gpt-5.4",
    )

    assert run_id == 123
    assert captured["path"] == "/api/agent/logs/runs"
    assert captured["data"]["source"] == "saas"


@pytest.mark.asyncio
async def test_report_tool_call_is_fire_and_forget(monkeypatch):
    monkeypatch.setenv("HC_CLIENT_URL", "https://hc.example.com")
    monkeypatch.setenv("HC_CLIENT_API_KEY", "agent-key")
    started = asyncio.Event()
    completed = False
    can_finish = asyncio.Event()

    async def fake_post(path, data):
        nonlocal completed
        started.set()
        await can_finish.wait()
        completed = True
        return {"ok": True}

    reporter = LogReporter()
    monkeypatch.setattr(reporter, "_post", fake_post)

    await reporter.report_tool_call(
        123,
        456,
        ToolExecuted(
            round=1,
            tool_name="sql_query",
            success=True,
            output_length=12,
            elapsed=0.2,
            arguments={"sql": "select 1"},
            output="ok",
        ),
    )

    await asyncio.wait_for(started.wait(), timeout=1)
    assert completed is False
    can_finish.set()
    await reporter.close()
    assert completed is True


@pytest.mark.asyncio
async def test_report_run_finish_flushes_pending_tool_calls(monkeypatch):
    monkeypatch.setenv("HC_CLIENT_URL", "https://hc.example.com")
    monkeypatch.setenv("HC_CLIENT_API_KEY", "agent-key")
    started = asyncio.Event()
    can_finish = asyncio.Event()
    order: list[str] = []

    async def fake_post(path, data):
        started.set()
        await can_finish.wait()
        order.append("tool")
        return {"ok": True}

    async def fake_put(path, data):
        order.append("finish")
        return {"ok": True}

    reporter = LogReporter()
    monkeypatch.setattr(reporter, "_post", fake_post)
    monkeypatch.setattr(reporter, "_put", fake_put)

    await reporter.report_tool_call(
        123,
        456,
        ToolExecuted(round=1, tool_name="sql_query", success=True, output_length=2, elapsed=0.1),
    )
    await asyncio.wait_for(started.wait(), timeout=1)

    finish_task = asyncio.create_task(
        reporter.report_run_finish(
            123,
            LoopFinished(status="completed", total_rounds=1, total_tokens=10, elapsed_seconds=1.0),
        )
    )
    await asyncio.sleep(0)
    assert order == []

    can_finish.set()
    await asyncio.wait_for(finish_task, timeout=1)
    assert order == ["tool", "finish"]


@pytest.mark.asyncio
async def test_log_reporter_uses_longer_timeout_for_remote_hc_client():
    assert log_reporter._TIMEOUT.total >= 10


@pytest.mark.asyncio
async def test_log_reporter_logs_exception_type_for_empty_message(monkeypatch):
    monkeypatch.setenv("HC_CLIENT_URL", "https://hc.example.com")
    monkeypatch.setenv("HC_CLIENT_API_KEY", "agent-key")
    captured = {}

    class FakeSession:
        def post(self, *args, **kwargs):
            raise TimeoutError()

    def fake_warning(event, **kwargs):
        captured["event"] = event
        captured.update(kwargs)

    reporter = LogReporter()
    monkeypatch.setattr(reporter, "_get_session", lambda: FakeSession())
    monkeypatch.setattr(log_reporter.logger, "warning", fake_warning)

    result = await reporter._post("/api/agent/logs/runs", {"ticket_id": 1})

    assert result is None
    assert captured["event"] == "log_report_failed"
    assert captured["error_type"] == "TimeoutError"
