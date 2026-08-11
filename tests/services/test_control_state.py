from __future__ import annotations

import json

import pytest

from zd_agent.config import AppConfig, ResourcesConfig
from zd_agent.services.control_state import control_path, is_paused, read_control


def _cfg(tmp_path) -> AppConfig:
    cfg = AppConfig()
    cfg.resources = ResourcesConfig(base_dir=str(tmp_path), state_dir=".state")
    return cfg


def test_read_control_defaults_to_not_paused(tmp_path) -> None:
    assert read_control(_cfg(tmp_path)) == {"paused": False}
    assert is_paused(_cfg(tmp_path)) is False


def test_read_control_returns_stored_pause_metadata(tmp_path) -> None:
    path = control_path(_cfg(tmp_path))
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "paused": True,
                "updated_at": "2026-06-05T15:00:00+08:00",
                "updated_by": "ops@dify.ai",
            }
        ),
        encoding="utf-8",
    )

    assert read_control(_cfg(tmp_path)) == {
        "paused": True,
        "updated_at": "2026-06-05T15:00:00+08:00",
        "updated_by": "ops@dify.ai",
    }
    assert is_paused(_cfg(tmp_path)) is True


def test_invalid_control_file_fails_closed_as_paused(tmp_path) -> None:
    path = control_path(_cfg(tmp_path))
    path.parent.mkdir(parents=True)
    path.write_text("{bad json", encoding="utf-8")

    assert read_control(_cfg(tmp_path)) == {
        "paused": True,
        "error": "control_state_unreadable",
    }
    assert is_paused(_cfg(tmp_path)) is True


@pytest.mark.parametrize("payload", ["null", "[]", '"paused"', "1"])
def test_non_object_control_state_fails_closed_as_paused(tmp_path, payload) -> None:
    path = control_path(_cfg(tmp_path))
    path.parent.mkdir(parents=True)
    path.write_text(payload, encoding="utf-8")

    assert read_control(_cfg(tmp_path)) == {
        "paused": True,
        "error": "control_state_unreadable",
    }


@pytest.mark.parametrize("paused", [None, 0, 1, "", "false", [], {}])
def test_non_boolean_paused_field_fails_closed(tmp_path, paused) -> None:
    path = control_path(_cfg(tmp_path))
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"paused": paused}), encoding="utf-8")

    assert read_control(_cfg(tmp_path)) == {
        "paused": True,
        "error": "control_state_unreadable",
    }
    assert is_paused(_cfg(tmp_path)) is True


def test_control_state_symlink_fails_closed_as_paused(tmp_path) -> None:
    path = control_path(_cfg(tmp_path))
    path.parent.mkdir(parents=True)
    path.symlink_to(tmp_path / "missing-control.json")

    assert read_control(_cfg(tmp_path)) == {
        "paused": True,
        "error": "control_state_unreadable",
    }
