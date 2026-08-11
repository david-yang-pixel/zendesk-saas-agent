from __future__ import annotations

from datetime import datetime
from hashlib import sha256
from typing import Any, cast

from zd_agent.casework.domain.models import AttachmentRef, FormIdentity, TicketSnapshot
from zd_agent.services.ticket_message_parsing import extract_form_data_from_msgs
from zd_agent.session.agent_session import AgentSession


def _required_event_time(value: object) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("latest customer message requires a timestamp")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("latest customer message has an invalid timestamp") from error
    if parsed.tzinfo is None:
        raise ValueError("latest customer message timestamp must include a timezone")
    return parsed


def _issue_text(message: dict[str, Any]) -> str:
    """Return customer issue prose without leaking profile-form fields into it."""
    classification = message.get("classification_body")
    if isinstance(classification, str) and classification.strip():
        return classification.strip()
    if message.get("content_type") == "formResponse":
        return ""
    body = message.get("body")
    return body.strip() if isinstance(body, str) else ""


def _questions(customer_messages: list[dict[str, Any]]) -> tuple[str, ...]:
    # Deduplicate from the tail so the last tuple item remains the customer's
    # latest actual issue (A -> B -> A must end in A, not B).
    seen: set[str] = set()
    reverse_unique: list[str] = []
    for message in reversed(customer_messages):
        text = _issue_text(message)
        if text and text not in seen:
            seen.add(text)
            reverse_unique.append(text)
    questions = tuple(reversed(reverse_unique))
    if not questions:
        raise ValueError("ticket snapshot requires customer issue text")
    return questions


def _attachment_refs(
    customer_messages: list[dict[str, Any]],
) -> tuple[AttachmentRef, ...]:
    urls: list[str] = []
    for message in customer_messages:
        raw_urls = message.get("image_urls")
        if not isinstance(raw_urls, list):
            continue
        url_values = cast(list[object], raw_urls)
        urls.extend(
            value.strip()
            for value in url_values
            if isinstance(value, str) and value.strip()
        )
    unique_urls = tuple(dict.fromkeys(urls))
    return tuple(
        AttachmentRef(
            attachment_id=sha256(url.encode("utf-8")).hexdigest()[:20],
            filename=f"customer-image-{index}",
            content_type="image",
            download_url=url,
        )
        for index, url in enumerate(unique_urls, start=1)
    )


def _requester_email(ticket: dict[str, Any]) -> str:
    requester = ticket.get("requester")
    if isinstance(requester, dict):
        value = cast(dict[str, object], requester).get("email")
        if isinstance(value, str) and value.strip():
            return value.strip().casefold()
    return ""


def _channel(ticket: dict[str, Any]) -> str:
    direct = ticket.get("channel")
    if isinstance(direct, str) and direct:
        return direct
    via = ticket.get("via")
    if isinstance(via, dict):
        value = cast(dict[str, object], via).get("channel")
        return value if isinstance(value, str) else ""
    return ""


def build_ticket_snapshot(
    *,
    session: AgentSession,
    customer_messages: list[dict[str, Any]],
    ticket: dict[str, Any],
) -> TicketSnapshot:
    """Convert the latest complete Zendesk read into an immutable case snapshot."""
    if not customer_messages:
        raise ValueError("ticket snapshot requires a current customer message")
    latest = customer_messages[-1]
    latest_id = str(latest.get("id") or "").strip()
    if not latest_id:
        raise ValueError("latest customer message requires an event ID")
    form = extract_form_data_from_msgs(customer_messages)
    return TicketSnapshot(
        ticket_id=session.ticket_id,
        generation=session.customer_context_generation,
        status=str(ticket.get("status") or "unknown"),
        channel=_channel(ticket),
        locale=str(ticket.get("locale") or ""),
        latest_event_id=latest_id,
        latest_event_at=_required_event_time(latest.get("created_at")),
        questions=_questions(customer_messages),
        form_identity=FormIdentity(
            email=form.get("email", ""),
            workspace_id=form.get("workspace_id", ""),
        ),
        requester_email=_requester_email(ticket),
        attachments=_attachment_refs(customer_messages),
    )
