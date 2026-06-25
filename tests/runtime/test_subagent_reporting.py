from types import SimpleNamespace
from typing import Any

import pytest

import zd_agent.runtime.subagent as subagent_module
from zd_agent.runtime.events import LoopFinished, LoopProgress, ToolExecuted
from zd_agent.session.agent_session import AgentSession
from zd_agent.tools.base import ToolRegistry


class _FakeProvider:
    def format_tool_result(self, tool_call_id: str, name: str, content: str) -> dict[str, str]:
        return {"role": "tool", "tool_call_id": tool_call_id, "name": name, "content": content}


@pytest.mark.asyncio
async def test_run_subagent_reports_child_run_round_tool_and_finish(monkeypatch):
    calls: dict[str, Any] = {"tool_calls": [], "finished": []}

    class FakeReporter:
        enabled = True

        async def report_run_start(self, **kwargs: Any) -> int:
            calls["start"] = kwargs
            return 987

        async def report_round(self, run_id: int, event: LoopProgress, model: str) -> int:
            calls["round"] = {"run_id": run_id, "event": event, "model": model}
            return 654

        async def report_tool_call(self, run_id: int, round_id: int, event: ToolExecuted) -> None:
            calls["tool_calls"].append({"run_id": run_id, "round_id": round_id, "event": event})

        async def report_run_finish(self, run_id: int, event: LoopFinished, **kwargs: Any) -> None:
            calls["finished"].append({"run_id": run_id, "event": event, "kwargs": kwargs})

        async def close(self) -> None:
            calls["closed"] = True

    async def fake_agent_loop(sub_session, _provider, message_store, *_args, **_kwargs):
        sub_session.round_count = 1
        sub_session.token_usage.prompt_tokens = 10
        sub_session.token_usage.completion_tokens = 3
        yield LoopProgress(round=1, prompt_tokens=10, completion_tokens=3, tool_call_count=1)
        yield ToolExecuted(round=1, tool_name="git_grep", success=True, output="ok")
        message_store.append_assistant({"role": "assistant", "content": "final child answer"})
        yield LoopFinished(status="completed", total_rounds=1, total_tokens=13, elapsed_seconds=0.2)

    cfg = SimpleNamespace(llm=SimpleNamespace(model="gpt-test", subagent_model=""))
    parent = AgentSession(ticket_id=2861)
    parent.remote_log_run_id = 123

    monkeypatch.setattr(subagent_module, "get_config", lambda: cfg)
    monkeypatch.setattr(subagent_module, "create_llm_provider", lambda *_args, **_kwargs: _FakeProvider())
    monkeypatch.setattr(subagent_module, "create_tool_registry", lambda: ToolRegistry())
    monkeypatch.setattr(subagent_module, "agent_loop", fake_agent_loop)
    monkeypatch.setattr(subagent_module, "LogReporter", FakeReporter)

    result = await subagent_module.run_subagent(
        "inspect source",
        ["git_grep"],
        "ctx",
        parent,
        log_metadata={
            "batch_id": "subagents-abc",
            "sibling_index": 0,
            "worker_role": "source_investigator",
            "question_preview": "inspect source",
            "task_preview": "inspect source",
        },
    )

    assert result.success is True
    assert "final child answer" in result.output
    assert calls["start"]["parent_run_id"] == 123
    assert calls["start"]["batch_id"] == "subagents-abc"
    assert calls["start"]["sibling_index"] == 0
    assert calls["start"]["worker_role"] == "source_investigator"
    assert calls["round"]["run_id"] == 987
    assert calls["round"]["model"] == "gpt-test"
    assert calls["tool_calls"][0]["run_id"] == 987
    assert calls["tool_calls"][0]["round_id"] == 654
    assert calls["finished"][0]["run_id"] == 987
    assert calls["finished"][0]["kwargs"]["total_input_tokens"] == 10
    assert calls["finished"][0]["kwargs"]["total_output_tokens"] == 3
    assert calls["closed"] is True


@pytest.mark.asyncio
async def test_run_subagent_closes_child_reporter_when_loop_raises(monkeypatch):
    calls: dict[str, Any] = {"finished": []}

    class FakeReporter:
        enabled = True

        async def report_run_start(self, **kwargs: Any) -> int:
            calls["start"] = kwargs
            return 988

        async def report_run_finish(self, run_id: int, event: LoopFinished, **kwargs: Any) -> None:
            calls["finished"].append({"run_id": run_id, "event": event, "kwargs": kwargs})

        async def close(self) -> None:
            calls["closed"] = True

    async def fake_agent_loop(sub_session, *_args, **_kwargs):
        sub_session.round_count = 1
        sub_session.token_usage.prompt_tokens = 8
        raise RuntimeError("boom")
        yield

    cfg = SimpleNamespace(llm=SimpleNamespace(model="gpt-test", subagent_model=""))
    parent = AgentSession(ticket_id=2861)
    parent.remote_log_run_id = 123

    monkeypatch.setattr(subagent_module, "get_config", lambda: cfg)
    monkeypatch.setattr(subagent_module, "create_llm_provider", lambda *_args, **_kwargs: _FakeProvider())
    monkeypatch.setattr(subagent_module, "create_tool_registry", lambda: ToolRegistry())
    monkeypatch.setattr(subagent_module, "agent_loop", fake_agent_loop)
    monkeypatch.setattr(subagent_module, "LogReporter", FakeReporter)

    with pytest.raises(RuntimeError, match="boom"):
        await subagent_module.run_subagent(
            "inspect source",
            ["git_grep"],
            "ctx",
            parent,
            log_metadata={
                "batch_id": "subagents-abc",
                "sibling_index": 0,
                "worker_role": "source_investigator",
            },
        )

    assert calls["start"]["parent_run_id"] == 123
    assert calls["finished"][0]["run_id"] == 988
    assert calls["finished"][0]["event"].status == "error"
    assert calls["finished"][0]["kwargs"]["total_input_tokens"] == 8
    assert calls["closed"] is True
