import pytest

from zd_agent.casework.adapters.ticket_snapshot import build_ticket_snapshot
from zd_agent.session.agent_session import AgentSession


def test_ticket_snapshot_uses_latest_customer_message_and_merged_form_identity() -> (
    None
):
    session = AgentSession(ticket_id=4321)
    session.customer_context_generation = 3
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
        "requester": {"email": "requester@example.test"},
    }

    snapshot = build_ticket_snapshot(
        session=session,
        customer_messages=messages,
        ticket=ticket,
    )

    assert snapshot.ticket_id == 4321
    assert snapshot.generation == 3
    assert snapshot.latest_event_id == "event-2"
    assert snapshot.questions == (
        "Initial description",
        "API requests are rejected after renewal.",
    )
    assert snapshot.form_identity.email == "owner@example.test"
    assert snapshot.form_identity.workspace_id == "target@example.test"
    assert snapshot.requester_email == "requester@example.test"
    assert snapshot.channel == "messaging"
    assert snapshot.attachments[0].download_url.endswith("image-1")
    assert snapshot.attachments[0].attachment_id


def test_ticket_snapshot_deduplicates_images_without_exposing_url_as_identifier() -> (
    None
):
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


def test_profile_only_form_response_keeps_prior_issue_and_advances_cursor() -> None:
    snapshot = build_ticket_snapshot(
        session=AgentSession(ticket_id=4321),
        customer_messages=[
            {
                "id": "issue-1",
                "created_at": "2026-08-11T07:00:00Z",
                "content_type": "text",
                "body": "The API quota is still unavailable after renewal.",
                "classification_body": "",
                "form_data": {},
            },
            {
                "id": "form-2",
                "created_at": "2026-08-11T07:01:00Z",
                "content_type": "formResponse",
                "body": "Name: Customer\nEmail: owner@example.test",
                "classification_body": "",
                "form_data": {"email": "owner@example.test"},
            },
        ],
        ticket={"status": "new"},
    )

    assert snapshot.latest_event_id == "form-2"
    assert snapshot.questions == ("The API quota is still unavailable after renewal.",)
    assert snapshot.form_identity.email == "owner@example.test"


def test_saas_intake_form_without_problem_builds_an_empty_issue_snapshot() -> None:
    """A completed pre-ticket form is a valid cursor even before an issue is sent."""
    snapshot = build_ticket_snapshot(
        session=AgentSession(ticket_id=4321),
        customer_messages=[
            {
                "id": "form-1",
                "created_at": "2026-08-11T07:00:00Z",
                "content_type": "formResponse",
                "body": (
                    "Name: Renato\nEmail: renato@example.test\n"
                    "Workspace ID: workspace-1\nPlan: Professional"
                ),
                "classification_body": "",
                "form_data": {
                    "email": "renato@example.test",
                    "workspace_id": "workspace-1",
                },
            },
        ],
        ticket={"status": "new"},
    )

    assert snapshot.latest_event_id == "form-1"
    assert snapshot.questions == ()
    assert snapshot.form_identity.email == "renato@example.test"


@pytest.mark.parametrize(
    "message",
    [
        "Hi",
        "Hello!",
        "Thanks",
        "Okay.",
        "Good morning",
        "Good afternoon!",
        "Greetings",
        "Hello everyone",
    ],
)
def test_greeting_after_form_is_not_mistaken_for_an_issue(message: str) -> None:
    snapshot = build_ticket_snapshot(
        session=AgentSession(ticket_id=4321),
        customer_messages=[
            {
                "id": "form-1",
                "created_at": "2026-08-11T07:00:00Z",
                "content_type": "formResponse",
                "body": "Email: owner@example.test",
                "form_data": {"email": "owner@example.test"},
            },
            {
                "id": "message-2",
                "created_at": "2026-08-11T07:01:00Z",
                "content_type": "text",
                "body": message,
            },
        ],
        ticket={"status": "new"},
    )

    assert snapshot.questions == ()
    assert snapshot.customer_messages[-1].body == message


def test_late_ack_does_not_replace_the_latest_actual_issue() -> None:
    snapshot = build_ticket_snapshot(
        session=AgentSession(ticket_id=4321),
        customer_messages=[
            {
                "id": "issue-1",
                "created_at": "2026-08-11T07:00:00Z",
                "body": "API requests return 429 after renewal.",
            },
            {
                "id": "ack-2",
                "created_at": "2026-08-11T07:01:00Z",
                "body": "Thanks!",
            },
        ],
        ticket={"status": "new"},
    )

    assert snapshot.latest_event_id == "ack-2"
    assert snapshot.questions == ("API requests return 429 after renewal.",)


def test_repeated_latest_issue_remains_the_last_question() -> None:
    snapshot = build_ticket_snapshot(
        session=AgentSession(ticket_id=4321),
        customer_messages=[
            {
                "id": "event-1",
                "created_at": "2026-08-11T07:00:00Z",
                "body": "Issue A",
            },
            {
                "id": "event-2",
                "created_at": "2026-08-11T07:01:00Z",
                "body": "Issue B",
            },
            {
                "id": "event-3",
                "created_at": "2026-08-11T07:02:00Z",
                "body": "Issue A",
            },
            {
                "id": "form-4",
                "created_at": "2026-08-11T07:03:00Z",
                "content_type": "formResponse",
                "body": "Email: owner@example.test",
                "form_data": {"email": "owner@example.test"},
            },
        ],
        ticket={"status": "new"},
    )

    assert snapshot.latest_event_id == "form-4"
    assert snapshot.questions == ("Issue B", "Issue A")
    assert snapshot.questions[-1] == "Issue A"


def test_ticket_snapshot_uses_only_current_ticket_requester_email() -> None:
    session = AgentSession(ticket_id=4321)

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
