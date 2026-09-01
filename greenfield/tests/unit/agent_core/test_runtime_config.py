import pytest

from casework_agent_core.runtime_config import (
    MappingSecretResolver,
    RuntimeConfigError,
    resolve_model_credentials,
)
from contracts.runtime import ModelBinding, RuntimeBootstrap


def _bootstrap() -> RuntimeBootstrap:
    return RuntimeBootstrap(
        config_revision="cfg-1",
        root_model=ModelBinding(
            provider="openai", model="root", credential_ref="secret/root"
        ),
        child_models=(),
        enabled_skills=("core-casework",),
        enabled_tools=("workspace_lookup",),
        model_digest="model",
        skill_digest="skill",
        tool_digest="tool",
    )


def test_secret_resolver_resolves_refs_only_and_never_changes_bootstrap() -> None:
    bootstrap = _bootstrap()
    resolved = resolve_model_credentials(
        bootstrap.root_model,
        resolver=MappingSecretResolver({"secret/root": "actual-secret"}),
    )

    assert resolved.api_key == "actual-secret"
    assert resolved.model == "root"
    assert "actual-secret" not in bootstrap.model_dump_json()


def test_secret_resolver_fails_closed_for_unknown_or_inherited_credentials() -> None:
    with pytest.raises(RuntimeConfigError, match="credential unavailable") as error:
        resolve_model_credentials(
            ModelBinding(provider="openai", model="child", credential_ref="secret/no"),
            resolver=MappingSecretResolver({}),
        )
    assert "secret/no" not in str(error.value)
