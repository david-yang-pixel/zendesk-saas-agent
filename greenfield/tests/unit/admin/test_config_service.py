from __future__ import annotations

import pytest

from casework_admin.config import (
    AdminConfig,
    AdminConfigService,
    ConfigConflict,
    InMemoryConfigRepository,
    ModelConfig,
    ProcessingRulesConfig,
    SubagentSlotConfig,
)


def _config(*, root_model: str = "gpt-root") -> AdminConfig:
    return AdminConfig(
        desired_state="running",
        root_model=ModelConfig(
            provider="openai",
            model=root_model,
            credential_ref="secret/root",
        ),
        subagent_slots=(
            SubagentSlotConfig(
                slot_id="investigator",
                enabled=True,
                provider="openai",
                model="gpt-child",
                credential_ref="secret/child",
                max_parallel=2,
            ),
        ),
        rules=ProcessingRulesConfig(
            saas_tag="saas",
            saas_assignee="saas-support",
            statuses=("new", "open"),
            human_aliases=("human", "真人"),
            reply_enabled=False,
            default_locale="en",
            footers={"en": "AI support footer", "ja": "AI サポート"},
        ),
        enabled_skills=("zendesk-casework-core",),
        enabled_tools=("zendesk_ticket", "sql_query"),
        max_tool_rounds=24,
    )


@pytest.mark.asyncio
async def test_save_creates_new_revision_without_mutating_history() -> None:
    repository = InMemoryConfigRepository(initial=_config())
    service = AdminConfigService(repository=repository)
    current = await service.current()

    updated = await service.save(
        expected_revision=current.revision,
        config=_config(root_model="gpt-root-v2"),
        actor="admin@example.com",
    )

    assert updated.revision != current.revision
    assert updated.config.root_model.model == "gpt-root-v2"
    assert (await repository.history())[0] == current
    assert (await repository.history())[1] == updated
    assert updated.actor == "admin@example.com"


@pytest.mark.asyncio
async def test_stale_expected_revision_is_rejected() -> None:
    repository = InMemoryConfigRepository(initial=_config())
    service = AdminConfigService(repository=repository)

    with pytest.raises(ConfigConflict):
        await service.save(
            expected_revision="stale",
            config=_config(root_model="gpt-root-v2"),
            actor="admin@example.com",
        )


def test_fixed_sandbox_resources_are_read_only_projection() -> None:
    projection = AdminConfigService.fixed_sandbox_policy()

    assert projection == {
        "attempt_1": {"cpu": 0.5, "memory_mib": 1024, "timeout_seconds": 1800},
        "attempt_2": {"cpu": 1.0, "memory_mib": 1024, "timeout_seconds": 1800},
        "retry_condition": "resource_exhausted_only",
    }


def test_subagent_requires_an_independent_model_configuration() -> None:
    with pytest.raises(ValueError, match="credential_ref"):
        SubagentSlotConfig(
            slot_id="investigator",
            enabled=True,
            provider="openai",
            model="gpt-child",
            credential_ref="",
            max_parallel=1,
        )


def test_model_configuration_accepts_references_but_not_raw_secret_material() -> None:
    with pytest.raises(ValueError, match="credential_ref"):
        ModelConfig(provider="openai", model="gpt-root", credential_ref="sk-live-token")
