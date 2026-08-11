"""Runtime control state for global agent operations."""

from __future__ import annotations

import json
import stat
from pathlib import Path
from typing import Any

from zd_agent.config import AppConfig, get_config


def control_path(cfg: AppConfig | None = None) -> Path:
    cfg = cfg or get_config()
    return Path(cfg.resources.base_dir) / cfg.resources.state_dir / "control.json"


def read_control(cfg: AppConfig | None = None) -> dict[str, Any]:
    path = control_path(cfg)
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return {"paused": False}
    except OSError:
        return {
            "paused": True,
            "error": "control_state_unreadable",
        }
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        return {
            "paused": True,
            "error": "control_state_unreadable",
        }
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise TypeError("control state must be a JSON object")
        paused = data.get("paused")
        if not isinstance(paused, bool):
            raise TypeError("control state paused field must be a boolean")
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError):
        return {
            "paused": True,
            "error": "control_state_unreadable",
        }
    return {
        "paused": paused,
        "updated_at": data.get("updated_at", ""),
        "updated_by": data.get("updated_by", ""),
    }


def is_paused(cfg: AppConfig | None = None) -> bool:
    return bool(read_control(cfg).get("paused"))
