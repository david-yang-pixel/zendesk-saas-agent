from typing import cast

import pytest

from casework_harness.runtime_bootstrap import (
    RuntimeBootstrapUnavailable,
    runtime_bootstrap_from_config,
)
from casework_ingress.postgres_admission import PostgresAdmission, _RuntimeConfig


def _config() -> dict[str, object]:
    return {
        "desired_state": "running",
        "root_model": {
            "provider": "openai",
            "model": "root",
            "credential_ref": "secret/root",
        },
        "subagent_slots": [
            {
                "slot_id": "disabled",
                "enabled": False,
                "provider": "openai",
                "model": "unused",
                "credential_ref": "secret/unused",
            },
            {
                "slot_id": "logs",
                "enabled": True,
                "provider": "openai",
                "model": "child",
                "credential_ref": "secret/logs",
                "context_window_tokens": 64000,
                "max_tool_rounds": 9,
                "max_parallel": 2,
            },
        ],
        "enabled_skills": ["core-casework"],
        "enabled_tools": ["workspace_lookup"],
        "rules": {
            "saas_tag": "saas",
            "saas_assignee": "SaaS",
            "statuses": ["new", "open"],
        },
    }


def test_projection_keeps_enabled_slots_and_only_credential_references() -> None:
    bootstrap = runtime_bootstrap_from_config(revision="cfg-1", config=_config())

    assert bootstrap.config_revision == "cfg-1"
    assert [slot.slot_id for slot in bootstrap.child_models] == ["logs"]
    assert bootstrap.child_models[0].max_parallel == 2
    assert bootstrap.child_models[0].context_window_tokens == 64000
    assert bootstrap.child_models[0].max_tool_rounds == 9
    assert "secret/root" in bootstrap.model_dump_json()
    assert "secret/logs" in bootstrap.model_dump_json()


def test_projection_rejects_inline_secret_in_persisted_config() -> None:
    config = _config()
    root = dict(cast(dict[str, object], config["root_model"]))
    root["credential_ref"] = "sk-live-token"
    config["root_model"] = root

    with pytest.raises((RuntimeBootstrapUnavailable, ValueError), match="credential"):
        runtime_bootstrap_from_config(revision="cfg-1", config=config)


def test_ingress_snapshot_and_harness_bootstrap_share_canonical_digests() -> None:
    config = _config()
    runtime = _RuntimeConfig.model_validate(config)
    snapshot = PostgresAdmission._frozen_config("cfg-1", runtime)
    bootstrap = runtime_bootstrap_from_config(revision="cfg-1", config=config)

    assert bootstrap.model_digest == snapshot.model_digest
    assert bootstrap.skill_digest == snapshot.skill_digest
    assert bootstrap.tool_digest == snapshot.tool_digest
