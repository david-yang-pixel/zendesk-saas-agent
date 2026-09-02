from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from contracts.runtime import (
    ChildModelBinding,
    ModelBinding,
    RuntimeBootstrap,
)
from contracts.sandbox import SandboxResources, SandboxStartRequest


def _bootstrap() -> RuntimeBootstrap:
    return RuntimeBootstrap(
        config_revision="cfg-1",
        root_model=ModelBinding(
            provider="openai",
            base_url="https://root-models.example.com",
            model="gpt-5.6-sol",
            credential_ref="secret/root",
        ),
        child_models=(
            ChildModelBinding(
                slot_id="logs",
                provider="openai",
                base_url="https://child-models.example.com",
                model="gpt-5.6-luna",
                credential_ref="secret/logs",
            ),
        ),
        enabled_skills=("core-casework",),
        enabled_tools=("workspace_lookup",),
        model_digest="sha256:model",
        skill_digest="sha256:skill",
        tool_digest="sha256:tool",
    )


def test_runtime_bootstrap_is_versioned_immutable_and_contains_refs_only() -> None:
    bootstrap = _bootstrap()

    assert bootstrap.version == 1
    assert bootstrap.root_model.credential_ref == "secret/root"
    assert bootstrap.root_model.base_url == "https://root-models.example.com"
    assert bootstrap.child_models[0].base_url == "https://child-models.example.com"
    assert bootstrap.child_models[0].model == "gpt-5.6-luna"
    encoded = bootstrap.model_dump_json()
    assert "secret/root" in encoded
    assert "secret/logs" in encoded
    assert "sk-live" not in encoded
    with pytest.raises(ValidationError):
        bootstrap.config_revision = "cfg-2"  # type: ignore[misc]


@pytest.mark.parametrize("value", ["sk-live-token", "Bearer abc", "api_key=abc"])
def test_model_binding_rejects_secret_material_in_credential_ref(value: str) -> None:
    with pytest.raises(ValidationError, match="credential_ref"):
        ModelBinding(
            provider="openai",
            model="gpt-5.6-sol",
            credential_ref=value,
        )


def test_sandbox_start_carries_the_one_time_runtime_bootstrap() -> None:
    request = SandboxStartRequest(
        sandbox_id="sandbox-1",
        job_id="job-1",
        session_id="session-1",
        ticket_id=3726,
        generation=1,
        resource_attempt=1,
        deadline=datetime(2026, 9, 1, tzinfo=UTC),
        resources=SandboxResources(),
        config_revision="cfg-1",
        model_digest="sha256:model",
        skill_digest="sha256:skill",
        tool_digest="sha256:tool",
        bootstrap=_bootstrap(),
    )

    assert request.bootstrap.config_revision == request.config_revision
    assert request.model_dump(mode="json")["bootstrap"]["root_model"][
        "credential_ref"
    ] == "secret/root"
