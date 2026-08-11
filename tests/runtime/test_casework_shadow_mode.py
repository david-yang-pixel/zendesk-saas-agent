from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock, MagicMock

from zd_agent.casework.domain.enums import ExecutionMode
from zd_agent.runtime.casework_shadow import maybe_run_casework_shadow
from zd_agent.runtime.llm.types import LLMProvider
from zd_agent.session.agent_session import AgentSession
from zd_agent.session.agent_session import SessionStatus
from zd_agent.tools.base import ToolRegistry


async def test_legacy_mode_does_not_start_shadow_evaluation(monkeypatch) -> None:
    shadow = AsyncMock()
    monkeypatch.setattr(
        "zd_agent.casework.adapters.shadow.run_casework_shadow_best_effort",
        shadow,
    )

    result = await maybe_run_casework_shadow(
        mode=ExecutionMode.LEGACY,
        session=AgentSession(ticket_id=4001),
        provider=cast(LLMProvider, object()),
        registry=ToolRegistry(),
        archive_dir=Path("ignored"),
        enable_vision=True,
    )

    assert result is None
    shadow.assert_not_awaited()


async def test_shadow_mode_runs_no_write_adapter(monkeypatch, tmp_path: Path) -> None:
    expected = object()
    shadow = AsyncMock(return_value=expected)
    monkeypatch.setattr(
        "zd_agent.casework.adapters.shadow.run_casework_shadow_best_effort",
        shadow,
    )
    session = AgentSession(ticket_id=4001)
    provider = cast(LLMProvider, object())
    registry = ToolRegistry()

    result = await maybe_run_casework_shadow(
        mode=ExecutionMode.SHADOW,
        session=session,
        provider=provider,
        registry=registry,
        archive_dir=tmp_path,
        enable_vision=True,
    )

    assert result is expected
    shadow.assert_awaited_once_with(
        session=session,
        provider=provider,
        registry=registry,
        archive_dir=tmp_path,
        enable_vision=True,
    )


async def test_agent_runner_invokes_shadow_after_legacy_loop(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from zd_agent.runtime import runner

    cfg = MagicMock()
    cfg.agent.guardrails = MagicMock()
    cfg.llm.model = "test-model"
    cfg.llm.enable_vision_input = True
    cfg.casework.mode = ExecutionMode.SHADOW
    cfg.casework.shadow_archive_dir = str(tmp_path)
    cfg.sandbox.cleanup_on_success = False
    cfg.sandbox.cleanup_on_failure = False
    monkeypatch.setattr(runner, "get_config", lambda: cfg)
    monkeypatch.setattr(runner, "_capture_customer_message_snapshot", AsyncMock())
    monkeypatch.setattr(runner, "ensure_archive", MagicMock())

    class FakeSandbox:
        async def create(self, ticket_id: int) -> str:
            return f"/tmp/{ticket_id}"

        async def cleanup(self) -> None:
            raise AssertionError("cleanup should be disabled")

    async def fake_tool_loop(session: AgentSession, *_args, **_kwargs) -> None:
        session.status = SessionStatus.COMPLETED

    provider = cast(LLMProvider, object())
    llm = MagicMock(_provider=provider)
    registry = ToolRegistry()
    shadow = AsyncMock()
    monkeypatch.setattr(runner, "create_sandbox", lambda: FakeSandbox())
    monkeypatch.setattr(runner, "create_tool_registry", lambda: registry)
    monkeypatch.setattr(runner, "LLMClient", lambda: llm)
    monkeypatch.setattr(
        runner,
        "Guardrails",
        lambda config: MagicMock(elapsed_seconds=0),
    )
    monkeypatch.setattr(runner, "run_tool_loop", fake_tool_loop)
    monkeypatch.setattr(runner, "maybe_run_casework_shadow", shadow)
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
    session = AgentSession(ticket_id=4001, dry_run=True)

    await runner.run_agent(session)

    shadow.assert_awaited_once_with(
        mode=ExecutionMode.SHADOW,
        session=session,
        provider=provider,
        registry=registry,
        archive_dir=tmp_path,
        enable_vision=True,
    )
