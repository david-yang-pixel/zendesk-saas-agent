"""Persist Zendesk Messaging conversation context by ticket id."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from zd_agent.config import get_config
from zd_agent.log import get_logger
from zd_agent.services.atomic_write import atomic_write_text

logger = get_logger(module="services.messaging_context")


def _context_path() -> Path:
    cfg = get_config()
    return (
        Path(cfg.resources.base_dir)
        / cfg.resources.state_dir
        / "messaging_context.json"
    )


def _read_contexts(path: Path) -> dict[str, dict[str, str]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except Exception as e:
        logger.warning(
            "messaging_context_read_failed",
            path=str(path),
            error=str(e)[:200],
        )
        return {}
    if not isinstance(raw, dict):
        return {}
    return {
        str(ticket_id): value
        for ticket_id, value in raw.items()
        if isinstance(value, dict)
    }


def save_messaging_context(
    *,
    ticket_id: int | str,
    conversation_id: str | None,
    app_id: str | None,
) -> None:
    """Remember Messaging conversation context for later tool calls."""
    conversation_id = (conversation_id or "").strip()
    app_id = (app_id or "").strip()
    if not conversation_id:
        return

    path = _context_path()
    contexts = _read_contexts(path)
    contexts[str(ticket_id)] = {
        "conversation_id": conversation_id,
        "app_id": app_id,
    }
    atomic_write_text(
        path,
        json.dumps(contexts, ensure_ascii=False, indent=2, sort_keys=True),
    )


def get_messaging_context(ticket_id: int | str) -> dict[str, str]:
    """Return persisted Messaging context for a ticket, if known."""
    value: Any = _read_contexts(_context_path()).get(str(ticket_id)) or {}
    if not isinstance(value, dict):
        return {}
    return {
        "conversation_id": str(value.get("conversation_id") or ""),
        "app_id": str(value.get("app_id") or ""),
    }
