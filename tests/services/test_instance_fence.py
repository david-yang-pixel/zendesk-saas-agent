from __future__ import annotations

import pytest

from zd_agent.services.instance_fence import InstanceFenceError, verify_active_instance


def test_instance_fence_is_disabled_outside_the_production_unit(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("ZD_AGENT_ENFORCE_INSTANCE_FENCE", raising=False)

    assert verify_active_instance(tmp_path) == ""


def test_persisted_marker_fences_manual_cli_without_environment_flag(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("ZD_AGENT_ENFORCE_INSTANCE_FENCE", raising=False)
    monkeypatch.setenv("ZD_AGENT_INSTANCE_ID", "old-host")
    (tmp_path / "active_instance_id").write_text("new-host\n")

    with pytest.raises(InstanceFenceError, match="fenced"):
        verify_active_instance(tmp_path)


def test_instance_fence_accepts_only_the_marked_host(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ZD_AGENT_ENFORCE_INSTANCE_FENCE", "1")
    monkeypatch.setenv("ZD_AGENT_INSTANCE_ID", "new-host")
    (tmp_path / "active_instance_id").write_text("new-host\n")

    assert verify_active_instance(tmp_path) == "new-host"

    (tmp_path / "active_instance_id").write_text("old-host\n")
    with pytest.raises(InstanceFenceError, match="fenced"):
        verify_active_instance(tmp_path)


def test_instance_fence_fails_closed_without_marker(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ZD_AGENT_ENFORCE_INSTANCE_FENCE", "true")
    monkeypatch.setenv("ZD_AGENT_INSTANCE_ID", "new-host")

    with pytest.raises(InstanceFenceError, match="marker"):
        verify_active_instance(tmp_path)


def test_instance_fence_rejects_symlink_marker(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ZD_AGENT_INSTANCE_ID", "new-host")
    target = tmp_path / "target"
    target.write_text("new-host\n")
    (tmp_path / "active_instance_id").symlink_to(target)

    with pytest.raises(InstanceFenceError, match="regular file"):
        verify_active_instance(tmp_path)
