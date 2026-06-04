"""Runtime control state for global agent operations."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from zd_agent.config import AppConfig, get_config


def control_path(cfg: AppConfig | None = None) -> Path:
    cfg = cfg or get_config()
    return Path(cfg.resources.base_dir) / cfg.resources.state_dir / "control.json"


def read_control(cfg: AppConfig | None = None) -> dict[str, Any]:
    path = control_path(cfg)
    if not path.exists():
        return {"paused": False}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"paused": False}
    return {
        "paused": bool(data.get("paused")),
        "updated_at": data.get("updated_at", ""),
        "updated_by": data.get("updated_by", ""),
    }


def is_paused(cfg: AppConfig | None = None) -> bool:
    return bool(read_control(cfg).get("paused"))
