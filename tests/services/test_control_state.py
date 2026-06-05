from __future__ import annotations

import json

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
        json.dumps({
            "paused": True,
            "updated_at": "2026-06-05T15:00:00+08:00",
            "updated_by": "ops@dify.ai",
        }),
        encoding="utf-8",
    )

    assert read_control(_cfg(tmp_path)) == {
        "paused": True,
        "updated_at": "2026-06-05T15:00:00+08:00",
        "updated_by": "ops@dify.ai",
    }
    assert is_paused(_cfg(tmp_path)) is True


def test_invalid_control_file_defaults_to_not_paused(tmp_path) -> None:
    path = control_path(_cfg(tmp_path))
    path.parent.mkdir(parents=True)
    path.write_text("{bad json", encoding="utf-8")

    assert read_control(_cfg(tmp_path)) == {"paused": False}
