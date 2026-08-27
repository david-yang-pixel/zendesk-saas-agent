from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from zd_agent.services.browser_relay_readiness import (
    BrowserRelayReadinessMonitor,
    RelayReadiness,
)


class FakeLifecycle:
    def __init__(self) -> None:
        self.interlock_active = False
        self.resume_requested = False
        self.engage_dependency_interlock = AsyncMock(side_effect=self._engage)
        self.clear_dependency_interlock = AsyncMock(side_effect=self._clear)

    async def _engage(self, **_kwargs):
        self.interlock_active = True
        return self.status()

    async def _clear(self, **_kwargs):
        self.interlock_active = False
        self.resume_requested = False
        return self.status()

    def status(self):
        return {
            "desired_state": "stopped" if self.interlock_active else "running",
            "process_running": False,
            "transition_status": "completed",
            "dependency_interlock": {
                "active": self.interlock_active,
                "dependency": "zendesk_browser_relay" if self.interlock_active else "",
                "reason": "login_required" if self.interlock_active else "",
                "resume_requested": self.resume_requested,
            },
        }


@pytest.mark.asyncio
async def test_failed_probe_engages_interlock_and_recovery_only_clears_it() -> None:
    lifecycle = FakeLifecycle()
    probes = [
        RelayReadiness(ok=False, status="login_required"),
        RelayReadiness(ok=True, status="logged_in"),
    ]

    async def probe():
        return probes.pop(0)

    monitor = BrowserRelayReadinessMonitor(
        lifecycle=lifecycle,
        readiness_url="http://127.0.0.1:8097/ready",
        api_key="secret",
        probe_fn=probe,
    )

    failed = await monitor.check_once()
    recovered = await monitor.check_once()

    assert failed.status == "login_required"
    lifecycle.engage_dependency_interlock.assert_awaited_once_with(
        dependency="zendesk_browser_relay",
        reason="login_required",
    )
    assert recovered.status == "logged_in"
    lifecycle.clear_dependency_interlock.assert_awaited_once_with(
        dependency="zendesk_browser_relay"
    )
    assert lifecycle.interlock_active is False


@pytest.mark.asyncio
async def test_repeated_failed_probe_does_not_repeat_lifecycle_transition() -> None:
    lifecycle = FakeLifecycle()

    async def probe():
        return RelayReadiness(ok=False, status="unreachable")

    monitor = BrowserRelayReadinessMonitor(
        lifecycle=lifecycle,
        readiness_url="http://127.0.0.1:8097/ready",
        api_key="secret",
        probe_fn=probe,
    )

    await monitor.check_once()
    await monitor.check_once()

    lifecycle.engage_dependency_interlock.assert_awaited_once()


@pytest.mark.asyncio
async def test_recovered_probe_retries_pending_auto_resume() -> None:
    lifecycle = FakeLifecycle()
    lifecycle.resume_requested = True

    async def probe():
        return RelayReadiness(ok=True, status="logged_in")

    monitor = BrowserRelayReadinessMonitor(
        lifecycle=lifecycle,
        readiness_url="http://127.0.0.1:8097/ready",
        api_key="secret",
        probe_fn=probe,
    )

    await monitor.check_once()

    lifecycle.clear_dependency_interlock.assert_awaited_once_with(
        dependency="zendesk_browser_relay"
    )


@pytest.mark.asyncio
async def test_busy_probe_does_not_change_lifecycle_state() -> None:
    lifecycle = FakeLifecycle()

    async def probe():
        return RelayReadiness(ok=True, status="busy")

    monitor = BrowserRelayReadinessMonitor(
        lifecycle=lifecycle,
        readiness_url="http://127.0.0.1:8097/ready",
        api_key="secret",
        probe_fn=probe,
    )

    result = await monitor.check_once()

    assert result.status == "busy"
    lifecycle.engage_dependency_interlock.assert_not_awaited()
    lifecycle.clear_dependency_interlock.assert_not_awaited()


def test_monitor_status_never_exposes_api_key() -> None:
    monitor = BrowserRelayReadinessMonitor(
        lifecycle=FakeLifecycle(),
        readiness_url="http://127.0.0.1:8097/ready",
        api_key="top-secret-relay-key",
    )

    rendered = repr(monitor.status())

    assert "top-secret-relay-key" not in rendered
    assert monitor.status()["status"] == "not_checked"
