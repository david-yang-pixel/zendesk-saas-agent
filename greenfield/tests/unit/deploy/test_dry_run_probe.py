from __future__ import annotations

from typing import Any, cast

from deploy import dry_run_probe


def test_probe_ignores_historical_failed_run(monkeypatch: Any) -> None:
    admitted_payload: dict[str, Any] = {}
    responses: list[dict[str, Any]] = [
        {
            "items": [
                {
                    "ticket_id": 3726,
                    "session_id": "old-session",
                    "status": "failed",
                    "active_question": {
                        "original_event_id": "event-original",
                        "clarification_event_ids": ["event-latest"],
                    },
                }
            ]
        },
        {"action": "enqueue"},
        {
            "items": [
                {
                    "ticket_id": 3726,
                    "session_id": "old-session",
                    "status": "failed",
                },
                {
                    "ticket_id": 3726,
                    "session_id": "new-session",
                    "status": "completed",
                },
            ]
        },
    ]

    def fake_request(
        _url: str,
        _payload: object | None = None,
        *,
        internal_token: str | None = None,
    ) -> dict[str, Any]:
        if "/api/runs" in _url:
            assert internal_token == "internal-token"
        elif isinstance(_payload, dict):
            admitted_payload.update(cast(dict[str, Any], _payload))
        return responses.pop(0)

    monkeypatch.setattr(
        dry_run_probe,
        "_json_request",
        fake_request,
    )

    assert (
        dry_run_probe.probe(
            ticket_id=3726,
            timeout_seconds=1,
            internal_token="internal-token",
        )
        == "new-session"
    )
    assert admitted_payload["snapshot_cursor"] == "event-latest"


def test_ticket_session_ids_filters_other_tickets_and_invalid_sessions() -> None:
    assert dry_run_probe._ticket_session_ids(  # pyright: ignore[reportPrivateUsage]
        {
            "items": [
                {"ticket_id": 3726, "session_id": "one"},
                {"ticket_id": 9999, "session_id": "other"},
                {"ticket_id": 3726, "session_id": None},
            ]
        },
        ticket_id=3726,
    ) == {"one"}
