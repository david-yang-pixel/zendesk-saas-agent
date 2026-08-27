from __future__ import annotations

import json
from pathlib import Path

import pytest

from zd_agent.casework.adapters.investigation_skill import load_investigation_skill
from zd_agent.services.instruction_store import (
    InstructionRevisionConflict,
    InstructionStore,
    InstructionValidationError,
)


def test_defaults_are_loaded_from_immutable_code_files(tmp_path: Path) -> None:
    store = InstructionStore(tmp_path)

    snapshot = store.snapshot()

    assert snapshot.revision == 0
    assert snapshot.overridden["skill_core"] is False
    assert "Dify Ticket Investigation" in snapshot.skill_core
    assert set(snapshot.scenario_references) == {
        "general",
        "async_processing",
        "billing_entitlement",
        "runtime_failure",
    }
    assert not store.path.exists()


def test_apply_is_atomic_revisioned_and_reset_removes_override(tmp_path: Path) -> None:
    store = InstructionStore(tmp_path)

    applied = store.apply(
        {
            "expected_revision": 0,
            "reason": "Tune operator guidance",
            "changes": {
                "operator_extra_instructions": "Prefer concise customer summaries.",
                "scenario_references": {"general": "Use the official docs first."},
            },
        },
        actor="admin",
        ip="127.0.0.1",
    )

    assert applied["revision"] == 1
    assert (
        applied["operator_extra_instructions"] == "Prefer concise customer summaries."
    )
    assert applied["scenario_references"]["general"] == "Use the official docs first."
    assert store.path.stat().st_mode & 0o777 == 0o600
    assert (
        json.loads(store.path.read_text())["operator_extra_instructions"]
        == "Prefer concise customer summaries."
    )

    with pytest.raises(InstructionRevisionConflict):
        store.apply(
            {
                "expected_revision": 0,
                "reason": "Stale write",
                "changes": {"operator_extra_instructions": "stale"},
            },
            actor="admin",
            ip="127.0.0.1",
        )

    reset = store.reset(
        {"expected_revision": 1, "reason": "Return to code defaults"},
        actor="admin",
        ip="127.0.0.1",
    )
    assert reset["revision"] == 2
    assert reset["operator_extra_instructions"] == ""
    assert not store.path.exists()
    no_op = store.apply(
        {
            "expected_revision": 2,
            "reason": "Keep code defaults",
            "changes": {},
        },
        actor="admin",
        ip="127.0.0.1",
    )
    assert no_op["changed"] is False
    assert no_op["revision"] == 2
    assert not store.path.exists()
    with pytest.raises(InstructionRevisionConflict):
        store.apply(
            {
                "expected_revision": 1,
                "reason": "Stale after reset",
                "changes": {"operator_extra_instructions": "stale"},
            },
            actor="admin",
            ip="127.0.0.1",
        )


def test_instruction_payload_rejects_unknown_fields_and_oversized_text(
    tmp_path: Path,
) -> None:
    store = InstructionStore(tmp_path)

    with pytest.raises(InstructionValidationError, match="unknown instruction fields"):
        store.apply(
            {"expected_revision": 0, "reason": "bad", "prompt": "override"},
            actor="admin",
            ip="127.0.0.1",
        )
    with pytest.raises(InstructionValidationError, match="maximum length"):
        store.apply(
            {
                "expected_revision": 0,
                "reason": "bad",
                "changes": {"operator_extra_instructions": "x" * 8_001},
            },
            actor="admin",
            ip="127.0.0.1",
        )


def test_loader_freezes_overrides_for_one_investigation(tmp_path: Path) -> None:
    store = InstructionStore(tmp_path)
    store.apply(
        {
            "expected_revision": 0,
            "reason": "Add bounded guidance",
            "changes": {"skill_core": "CUSTOM SKILL CORE"},
        },
        actor="admin",
        ip="127.0.0.1",
    )
    loader = store.loader()
    store.apply(
        {
            "expected_revision": 1,
            "reason": "Change next generation",
            "changes": {"skill_core": "NEXT SKILL CORE"},
        },
        actor="admin",
        ip="127.0.0.1",
    )

    frozen = loader("Is this API behavior supported?")
    current = load_investigation_skill(
        "Is this API behavior supported?", instruction_store=store
    )
    assert "CUSTOM SKILL CORE" in frozen.instructions
    assert "NEXT SKILL CORE" in current.instructions


def test_saving_effective_defaults_does_not_pin_code_defaults(tmp_path: Path) -> None:
    store = InstructionStore(tmp_path)
    defaults = store.defaults()

    applied = store.apply(
        {
            "expected_revision": 0,
            "reason": "Add operator-only guidance",
            "changes": {
                "operator_extra_instructions": "Prefer concise summaries.",
                "skill_core": defaults.skill_core,
                "scenario_references": defaults.scenario_references,
            },
        },
        actor="admin",
        ip="127.0.0.1",
    )

    assert applied["overridden"]["operator_extra_instructions"] is True
    assert applied["overridden"]["skill_core"] is False
    assert all(
        applied["overridden"][f"scenario_references.{key}"] is False
        for key in defaults.scenario_references
    )
    stored = json.loads(store.path.read_text())
    assert stored["skill_core_override"] is None
    assert stored["scenario_reference_overrides"] == {}


def test_reset_revision_tombstone_ignores_an_older_override_file(
    tmp_path: Path,
) -> None:
    store = InstructionStore(tmp_path)
    store.apply(
        {
            "expected_revision": 0,
            "reason": "Temporary guidance",
            "changes": {"operator_extra_instructions": "temporary"},
        },
        actor="admin",
        ip="127.0.0.1",
    )
    old_payload = store.path.read_text()
    store.reset(
        {"expected_revision": 1, "reason": "Return to defaults"},
        actor="admin",
        ip="127.0.0.1",
    )

    # Simulate a crash after the reset revision was committed but before the
    # older override file was removed.
    store.path.write_text(old_payload)
    snapshot = store.snapshot()
    assert snapshot.revision == 2
    assert snapshot.operator_extra_instructions == ""
    assert snapshot.overridden["operator_extra_instructions"] is False
