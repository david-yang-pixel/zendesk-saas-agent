import pytest

from casework_ingress.messaging import parse_messaging_payload

CONTACT = "Name: Test\nEmail: test@example.invalid\nWorkspace ID: example\nPlan: Pro"


@pytest.mark.parametrize("kind", ["text", "formResponse"])
@pytest.mark.parametrize("question", ["", "\n\nWhy was I charged twice?"])
def test_contact_fields_with_or_without_a_customer_question(kind: str, question: str) -> None:
    event = parse_messaging_payload({
        "id": "evt-1", "ticket_id": 5691,
        "event": {"actor": {"type": "user"}, "message": {
            "id": "m-1", "content": {"type": kind, "text": CONTACT + question},
        }},
    })
    assert event.form_only is (not question)
    assert event.message_text == CONTACT + question


def test_a_contact_form_with_an_attachment_can_trigger_work() -> None:
    event = parse_messaging_payload({
        "id": "evt-1", "ticket_id": 5691,
        "event": {"actor": {"type": "user"}, "message": {
            "id": "m-1", "content": {"type": "formResponse", "text": CONTACT,
            "attachments": [{"content_url": "https://example.invalid/receipt.pdf"}]},
        }},
    })
    assert event.has_attachments
    assert not event.form_only
