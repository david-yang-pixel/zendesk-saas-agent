"""Compatibility exports for the waiting poller runtime."""
from __future__ import annotations

from zd_agent.services.waiting_poller import (
    AGENT_ASKED_TAG,
    _alert_feishu,
    _process_one,
    run_waiting_poller,
    scan_once,
)

__all__ = [
    "AGENT_ASKED_TAG",
    "_alert_feishu",
    "_process_one",
    "run_waiting_poller",
    "scan_once",
]
