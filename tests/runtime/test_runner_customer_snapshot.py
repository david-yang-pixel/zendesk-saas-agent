from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from zd_agent.session.agent_session import AgentSession, SessionStatus


@pytest.mark.asyncio
async def test_run_agent_captures_customer_snapshot_before_tool_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from zd_agent.runtime import runner

    cfg = MagicMock()
    cfg.agent.guardrails = MagicMock()
    cfg.llm.model = "test-model"
    cfg.sandbox.cleanup_on_success = False
    cfg.sandbox.cleanup_on_failure = False
    monkeypatch.setattr(runner, "get_config", lambda: cfg)
    monkeypatch.setattr(runner, "_ensure_archive", lambda *_args, **_kwargs: None)

    class FakeSandbox:
        async def create(self, ticket_id: int) -> str:
            return f"/tmp/{ticket_id}"

        async def cleanup(self) -> None:
            raise AssertionError("cleanup should be disabled in this test")

    observed_snapshot: dict[str, int | str] = {}

    async def fake_tool_loop(session: AgentSession, *_args, **_kwargs) -> None:
        observed_snapshot["count"] = session.customer_msgs_snapshot_count
        observed_snapshot["last_id"] = session.customer_msgs_snapshot_last_id
        observed_snapshot["captured"] = session.customer_msgs_snapshot_captured
        session.status = SessionStatus.COMPLETED

    fetch_messages = AsyncMock(return_value=(
        [
            {"id": "msg-1", "body": "Initial issue"},
            {"id": "msg-2", "body": "Additional context"},
        ],
        {"id": 2809},
    ))
    monkeypatch.setattr(runner, "create_sandbox", lambda: FakeSandbox())
    monkeypatch.setattr(runner, "create_tool_registry", lambda: MagicMock())
    monkeypatch.setattr(runner, "LLMClient", lambda: MagicMock())
    monkeypatch.setattr(
        runner,
        "Guardrails",
        lambda config: MagicMock(elapsed_seconds=0),
    )
    monkeypatch.setattr(runner, "run_tool_loop", fake_tool_loop)
    monkeypatch.setattr(
        "zd_agent.services.ticket_messages._fetch_customer_messages",
        fetch_messages,
    )
    monkeypatch.setattr(
        "zd_agent.runtime.customer_profile.prefetch_customer_profile",
        AsyncMock(return_value={}),
    )
    monkeypatch.setattr(
        "zd_agent.tools.saas_version.resolve_deployed_version",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "zd_agent.runtime.prompt_loader.build_static_system_prompt",
        lambda: "system",
    )

    session = AgentSession(ticket_id=2809, dry_run=True)
    await runner.run_agent(session)

    assert observed_snapshot == {
        "count": 2,
        "last_id": "msg-2",
        "captured": True,
    }
    fetch_messages.assert_awaited_once_with(2809)


@pytest.mark.asyncio
async def test_run_agent_stops_when_customer_snapshot_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from zd_agent.runtime import runner

    cfg = MagicMock()
    cfg.agent.guardrails = MagicMock()
    cfg.llm.model = "test-model"
    monkeypatch.setattr(runner, "get_config", lambda: cfg)
    monkeypatch.setattr(
        runner,
        "Guardrails",
        lambda config: MagicMock(elapsed_seconds=0),
    )
    monkeypatch.setattr(
        "zd_agent.services.ticket_messages._fetch_customer_messages",
        AsyncMock(return_value=([], None)),
    )
    create_sandbox = MagicMock()
    ensure_archive = MagicMock()
    log_info = MagicMock()
    reporter = MagicMock()
    reporter.enabled = True
    reporter.report_run_start = AsyncMock(return_value=42)
    reporter.report_run_finish = AsyncMock()
    reporter.close = AsyncMock()
    monkeypatch.setattr(runner, "create_sandbox", create_sandbox)
    monkeypatch.setattr(runner, "_ensure_archive", ensure_archive)
    monkeypatch.setattr(runner.logger, "info", log_info)
    monkeypatch.setattr(
        "zd_agent.runtime.runner_reporting.LogReporter",
        lambda: reporter,
    )

    session = AgentSession(ticket_id=2809, dry_run=True)
    await runner.run_agent(session)

    assert session.status is SessionStatus.FAILED
    assert "customer-message snapshot" in (session.error_message or "")
    create_sandbox.assert_not_called()
    ensure_archive.assert_called_once()
    assert any(call.args == ("agent_done",) for call in log_info.call_args_list)
    reporter.report_run_start.assert_awaited_once()
    reporter.report_run_finish.assert_awaited_once()
    finish_event = reporter.report_run_finish.await_args.args[1]
    assert finish_event.status == "error"
    assert "customer-message snapshot" in finish_event.reason
    reporter.close.assert_awaited_once()
