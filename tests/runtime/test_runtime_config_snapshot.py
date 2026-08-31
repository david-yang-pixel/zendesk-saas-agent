from __future__ import annotations

from zd_agent.config import (
    AppConfig,
    EnvSettings,
    LLMConfig,
    SubagentConfig,
    SubagentLLMConfig,
    SubagentSlotConfig,
)
from zd_agent.runtime.config_snapshot import build_casework_runtime_snapshot
from zd_agent.tools.base import ToolRegistry


def _configured_app() -> AppConfig:
    return AppConfig(
        llm=LLMConfig(provider="openai", model="main-model"),
        subagents=SubagentConfig(
            enabled=True,
            slots=[
                SubagentSlotConfig(
                    id="reader",
                    llm=SubagentLLMConfig(
                        provider="anthropic",
                        model="child-model",
                        api_key="child-secret",
                        base_url="https://child.example.test/v1",
                    ),
                )
            ],
        ),
    )


def test_runtime_snapshot_freezes_main_and_child_models_independently() -> None:
    cfg = _configured_app()
    env = EnvSettings(
        openai_api_key="main-secret",
        anthropic_api_key="global-anthropic-secret",
    )

    snapshot = build_casework_runtime_snapshot(
        cfg,
        env=env,
        registry=ToolRegistry(),
        config_revision="admin-audit-42",
    )
    cfg.llm.model = "changed-main"
    cfg.subagents.slots[0].llm.model = "changed-child"

    assert snapshot.main_llm.model == "main-model"
    assert snapshot.main_llm.api_key == "main-secret"
    assert snapshot.subagent_slots[0].llm.model == "child-model"
    assert snapshot.subagent_slots[0].llm.api_key == "child-secret"
    assert snapshot.subagent_slots[0].llm.api_key != env.anthropic_api_key
    assert snapshot.config_revision == "admin-audit-42"
    assert snapshot.config_digest


def test_child_snapshot_never_falls_back_to_main_or_provider_environment_key() -> None:
    cfg = _configured_app()
    cfg.subagents.slots[0].llm.api_key = ""
    env = EnvSettings(
        openai_api_key="main-secret",
        anthropic_api_key="global-anthropic-secret",
    )

    snapshot = build_casework_runtime_snapshot(
        cfg,
        env=env,
        registry=ToolRegistry(),
    )

    assert snapshot.subagent_slots[0].llm.api_key == ""
    assert snapshot.subagent_slots[0].llm.model == "child-model"


def test_disabled_subagent_slots_are_excluded_from_runtime_snapshot() -> None:
    cfg = _configured_app()
    cfg.subagents.slots[0].enabled = False
    cfg.subagents.enabled = False

    snapshot = build_casework_runtime_snapshot(
        cfg,
        env=EnvSettings(),
        registry=ToolRegistry(),
    )

    assert snapshot.subagents_enabled is False
    assert snapshot.subagent_slots == ()


def test_runtime_digest_changes_when_orchestration_limits_change() -> None:
    cfg = _configured_app()
    first = build_casework_runtime_snapshot(
        cfg,
        env=EnvSettings(),
        registry=ToolRegistry(),
    )
    cfg.subagents.max_parallel = 3
    second = build_casework_runtime_snapshot(
        cfg,
        env=EnvSettings(),
        registry=ToolRegistry(),
    )

    assert first.config_digest != second.config_digest
