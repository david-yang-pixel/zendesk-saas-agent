from __future__ import annotations

from zd_agent.services.agent_lifecycle import ACCEPT_AFTER_ENV
from zd_agent.services.event_watermark import is_after_online_watermark


def test_watermark_accepts_only_strictly_newer_events(monkeypatch) -> None:
    monkeypatch.setenv(ACCEPT_AFTER_ENV, "1787702400")

    assert is_after_online_watermark("2026-08-26T00:00:01Z") is True
    assert is_after_online_watermark("2026-08-26T00:00:00Z") is False
    assert is_after_online_watermark("2026-08-25T23:59:59Z") is False
    assert is_after_online_watermark(None) is False


def test_missing_watermark_preserves_legacy_behavior(monkeypatch) -> None:
    monkeypatch.delenv(ACCEPT_AFTER_ENV, raising=False)

    assert is_after_online_watermark(None) is True
