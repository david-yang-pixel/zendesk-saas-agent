import pytest

from zd_agent.casework.adapters.ticket_snapshot import build_ticket_snapshot
from zd_agent.session.agent_session import AgentSession


def test_ticket_snapshot_uses_latest_customer_message_and_merged_form_identity() -> None:
    session = AgentSession(ticket_id=4321)
    session.customer_context_generation = 3
    session.customer_profile = {"requester_email": "requester@example.test"}
    messages = [
        {
            "id": "event-1",
            "created_at": "2026-08-11T07:00:00Z",
            "body": "Initial description",
            "form_data": {"email": "owner@example.test"},
            "image_urls": [],
        },
        {
            "id": "event-2",
            "created_at": "2026-08-11T07:05:00+00:00",
            "body": "Profile fallback text",
            "classification_body": "API requests are rejected after renewal.",
            "form_data": {"workspace_id": "target@example.test"},
            "image_urls": ["https://example.test/sc/attachments/v2/image-1"],
        },
    ]
    ticket = {
        "status": "new",
        "via": {"channel": "messaging"},
        "locale": "en-US",
    }

    snapshot = build_ticket_snapshot(
        session=session,
        customer_messages=messages,
        ticket=ticket,
    )

    assert snapshot.ticket_id == 4321
    assert snapshot.generation == 3
    assert snapshot.latest_event_id == "event-2"
    assert snapshot.questions == ("API requests are rejected after renewal.",)
    assert snapshot.form_identity.email == "owner@example.test"
    assert snapshot.form_identity.workspace_id == "target@example.test"
    assert snapshot.requester_email == "requester@example.test"
    assert snapshot.channel == "messaging"
    assert snapshot.attachments[0].download_url.endswith("image-1")
    assert snapshot.attachments[0].attachment_id


def test_ticket_snapshot_deduplicates_images_without_exposing_url_as_identifier() -> None:
    url = "https://example.test/sc/attachments/v2/sensitive-token"
    snapshot = build_ticket_snapshot(
        session=AgentSession(ticket_id=4321),
        customer_messages=[
            {
                "id": "event-1",
                "created_at": "2026-08-11T07:00:00Z",
                "body": "See screenshot",
                "image_urls": [url, url],
            },
        ],
        ticket={"status": "new"},
    )

    assert len(snapshot.attachments) == 1
    assert snapshot.attachments[0].attachment_id not in url
    assert len(snapshot.attachments[0].attachment_id) == 20


def test_current_ticket_requester_email_overrides_stale_session_profile() -> None:
    session = AgentSession(ticket_id=4321)
    session.customer_profile = {"requester_email": "stale@example.test"}

    snapshot = build_ticket_snapshot(
        session=session,
        customer_messages=[
            {
                "id": "event-1",
                "created_at": "2026-08-11T07:00:00Z",
                "body": "Current question",
            },
        ],
        ticket={
            "status": "new",
            "requester": {"email": "current@example.test"},
        },
    )

    assert snapshot.requester_email == "current@example.test"


def test_ticket_snapshot_fails_closed_without_current_customer_message() -> None:
    with pytest.raises(ValueError, match="customer message"):
        build_ticket_snapshot(
            session=AgentSession(ticket_id=4321),
            customer_messages=[],
            ticket={"status": "new"},
        )
