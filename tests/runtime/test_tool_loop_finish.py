from typing import Any

import pytest

import zd_agent.runtime.tool_loop as tool_loop_module
from zd_agent.config import GuardrailsConfig
from zd_agent.runtime.events import LoopProgress
from zd_agent.runtime.guardrails import Guardrails
from zd_agent.runtime.tool_loop import run_tool_loop
from zd_agent.session.agent_session import AgentSession
from zd_agent.tools.base import ToolRegistry


class _FakeLLM:
    _provider = object()


@pytest.mark.asyncio
async def test_tool_loop_reports_failed_finish_when_agent_loop_exits_without_finish(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reporter_instances: list[Any] = []

    class FakeReporter:
        enabled = True

        def __init__(self) -> None:
            self.finished = []
            self.closed = False
            reporter_instances.append(self)

        async def report_run_start(self, **kwargs: Any) -> int:
            return 123

        async def report_round(self, *args: Any, **kwargs: Any) -> int:
            return 456

        async def report_tool_call(self, *args: Any, **kwargs: Any) -> None:
            return None

        async def report_run_finish(self, run_id: int, event: Any, **kwargs: Any) -> None:
            self.finished.append((run_id, event, kwargs))

        async def close(self) -> None:
            self.closed = True

    async def fake_agent_loop(*args: Any, **kwargs: Any):
        session = args[0]
        session.round_count = 1
        session.token_usage.prompt_tokens = 10
        yield LoopProgress(round=1, message_count=1, token_count=10)

    monkeypatch.setattr(tool_loop_module, "LogReporter", FakeReporter)
    monkeypatch.setattr(tool_loop_module, "agent_loop", fake_agent_loop)

    await run_tool_loop(
        AgentSession(ticket_id=2981),
        _FakeLLM(),
        ToolRegistry(),
        Guardrails(GuardrailsConfig()),
        trigger_type="webhook",
    )

    reporter = reporter_instances[0]
    assert reporter.closed is True
    assert len(reporter.finished) == 1
    run_id, event, kwargs = reporter.finished[0]
    assert run_id == 123
    assert event.status == "error"
    assert event.reason == "agent loop exited before finish event"
    assert kwargs["total_input_tokens"] == 10
