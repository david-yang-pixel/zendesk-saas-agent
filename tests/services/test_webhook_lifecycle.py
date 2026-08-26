from __future__ import annotations

from unittest.mock import MagicMock

from aiohttp import web

from zd_agent.services.app_context import install_agent_lifecycle
from zd_agent.services.webhook_lifecycle import drop_when_agent_offline
from zd_agent.services.webhook_lifecycle import drop_when_before_online_watermark


class _Lifecycle:
    def __init__(self, accepting: bool, accept_after: float = 0) -> None:
        self.accepting = accepting
        self.boundary = accept_after
        self.drops: list[dict[str, str]] = []

    def accepting_events(self) -> bool:
        return self.accepting

    def record_dropped_event(self, **payload: str) -> None:
        self.drops.append(payload)

    def status(self) -> dict[str, str]:
        return {"status": "running" if self.accepting else "stopped"}

    def accept_after(self) -> float:
        return self.boundary


def test_valid_offline_event_is_acknowledged_and_deduplicated() -> None:
    app = web.Application()
    lifecycle = _Lifecycle(accepting=False)
    install_agent_lifecycle(app=app, lifecycle=lifecycle)
    request = MagicMock()
    request.app = app
    dedup = MagicMock()

    response = drop_when_agent_offline(
        request,
        source="messaging_webhook",
        ticket_id="4051",
        dedup=dedup,
        dedup_key="event-1",
    )

    assert response is not None
    assert response.status == 200
    dedup.add.assert_called_once_with("event-1")
    assert lifecycle.drops == [
        {"source": "messaging_webhook", "ticket_id": "4051"}
    ]


def test_running_event_continues_to_durable_queue_path() -> None:
    app = web.Application()
    install_agent_lifecycle(app=app, lifecycle=_Lifecycle(accepting=True))
    request = MagicMock()
    request.app = app

    assert (
        drop_when_agent_offline(
            request,
            source="zendesk_webhook",
            ticket_id="4051",
            dedup=MagicMock(),
            dedup_key="event-2",
        )
        is None
    )


def test_delayed_offline_epoch_event_is_acknowledged_after_restart() -> None:
    app = web.Application()
    lifecycle = _Lifecycle(accepting=True, accept_after=200)
    install_agent_lifecycle(app=app, lifecycle=lifecycle)
    request = MagicMock()
    request.app = app
    dedup = MagicMock()

    response = drop_when_before_online_watermark(
        request,
        source="messaging_webhook",
        ticket_id="4051",
        event_time=199,
        dedup=dedup,
        dedup_key="event-old",
    )

    assert response is not None
    assert response.status == 200
    dedup.add.assert_called_once_with("event-old")
    assert lifecycle.drops[0]["source"].endswith(":offline_watermark")


def test_event_without_immutable_occurrence_time_is_dropped_after_restart() -> None:
    app = web.Application()
    lifecycle = _Lifecycle(accepting=True, accept_after=200)
    install_agent_lifecycle(app=app, lifecycle=lifecycle)
    request = MagicMock()
    request.app = app
    dedup = MagicMock()

    response = drop_when_before_online_watermark(
        request,
        source="zendesk_webhook",
        ticket_id="4051",
        event_time=None,
        dedup=dedup,
        dedup_key="event-unproven",
    )

    assert response is not None
    assert response.status == 200
    dedup.add.assert_called_once_with("event-unproven")
    assert lifecycle.drops[0]["source"].endswith(":offline_watermark")
