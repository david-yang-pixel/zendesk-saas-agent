"""Global pytest safety guards.

Local development often has real production credentials in ``.env``. Tests must
not inherit notification webhooks from that file because some orchestration
tests intentionally exercise escalation paths.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _disable_real_feishu_notifications(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep pytest from sending real Feishu webhook notifications.

    Setting empty env vars is intentional: pydantic-settings gives environment
    variables precedence over ``.env`` values, so this masks any real local
    webhook while still allowing individual tests to monkeypatch fake values.
    """
    monkeypatch.setenv("FEISHU_WEBHOOK_URL", "")
    monkeypatch.setenv("FEISHU_WEBHOOK_SECRET", "")
    monkeypatch.setenv("NOTIFICATION_SERVICE_URL", "")
    monkeypatch.setenv("NOTIFICATION_SERVICE_API_KEY", "")
