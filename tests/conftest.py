"""Global pytest safety guards.

Local development often has real production credentials in ``.env``. Tests must
not inherit the notification-service endpoint because orchestration tests
intentionally exercise ownership and completion paths.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _disable_real_notifications(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep pytest from calling the real notification service.

    Setting empty env vars is intentional: pydantic-settings gives environment
    variables precedence over ``.env`` values, so this masks any real local
    webhook while still allowing individual tests to monkeypatch fake values.
    """
    monkeypatch.setenv("NOTIFICATION_SERVICE_URL", "")
    monkeypatch.setenv("NOTIFICATION_SERVICE_API_KEY", "")
