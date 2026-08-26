from __future__ import annotations

from datetime import datetime
from hashlib import sha256
from typing import Any, cast

from zd_agent.casework.domain.models import (
    AttachmentRef,
    CustomerMessageEvent,
    FormIdentity,
    TicketSnapshot,
)
from zd_agent.casework.policies.customer_message import (
    is_definitely_non_substantive_message,
)
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


def _is_non_issue_message(text: str) -> bool:
    """Recognize only short, standalone greetings and acknowledgements."""
    return is_definitely_non_substantive_message(text)


def _questions(customer_messages: list[dict[str, Any]]) -> tuple[str, ...]:
    # Deduplicate from the tail so the last tuple item remains the customer's
    # latest actual issue (A -> B -> A must end in A, not B).
    seen: set[str] = set()
    reverse_unique: list[str] = []
    for message in reversed(customer_messages):
        text = _issue_text(message)
        if text and not _is_non_issue_message(text) and text not in seen:
            seen.add(text)
            reverse_unique.append(text)
    return tuple(reversed(reverse_unique))


def _customer_message_events(
    customer_messages: list[dict[str, Any]],
) -> tuple[CustomerMessageEvent, ...]:
    events: list[CustomerMessageEvent] = []
    for message in customer_messages:
        body = _issue_text(message)
        if not body:
            continue
        event_id = str(message.get("id") or "").strip()
        if not event_id:
            raise ValueError("customer message requires an event ID")
        events.append(
            CustomerMessageEvent(
                event_id=event_id,
                occurred_at=_required_event_time(message.get("created_at")),
                body=body,
            ),
        )
    return tuple(events)


def _attachment_refs(
    customer_messages: list[dict[str, Any]],
) -> tuple[AttachmentRef, ...]:
    refs: list[AttachmentRef] = []
    seen_urls: set[str] = set()
    for message in customer_messages:
        raw_attachments = message.get("attachments")
        if isinstance(raw_attachments, list):
            for raw in cast(list[object], raw_attachments):
                if not isinstance(raw, dict):
                    continue
                attachment = cast(dict[str, object], raw)
                raw_url = next(
                    (
                        attachment.get(key)
                        for key in (
                            "content_url",
                            "mapped_content_url",
                            "media_url",
                            "url",
                        )
                        if attachment.get(key)
                    ),
                    "",
                )
                url = raw_url.strip() if isinstance(raw_url, str) else ""
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)
                raw_id = attachment.get("id") or attachment.get("attachment_id")
                attachment_id = (
                    str(raw_id).strip()
                    if raw_id not in (None, "")
                    else sha256(url.encode("utf-8")).hexdigest()[:20]
                )
                raw_name = (
                    attachment.get("file_name")
                    or attachment.get("filename")
                    or attachment.get("name")
                    or attachment.get("alt_text")
                    or "customer-attachment"
                )
                raw_type = (
                    attachment.get("content_type")
                    or attachment.get("mime_type")
                    or attachment.get("type")
                    or ""
                )
                refs.append(
                    AttachmentRef(
                        attachment_id=attachment_id,
                        filename=str(raw_name).strip() or "customer-attachment",
                        content_type=str(raw_type).strip(),
                        download_url=url,
                    )
                )
        raw_urls = message.get("image_urls")
        if not isinstance(raw_urls, list):
            continue
        for value in cast(list[object], raw_urls):
            url = value.strip() if isinstance(value, str) else ""
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            refs.append(
                AttachmentRef(
                    attachment_id=sha256(url.encode("utf-8")).hexdigest()[:20],
                    filename=f"customer-image-{len(refs) + 1}",
                    content_type="image",
                    download_url=url,
                )
            )
    return tuple(refs)


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
    questions = _questions(customer_messages)
    clarification_pending = bool(session.prior_customer_required_fields)
    identity_clarification_pending = bool(
        {
            "target_workspace_choice",
            "target_workspace_identity",
        }
        & set(session.prior_customer_required_fields)
    )
    active_question = (
        session.prior_issue_question
        if clarification_pending and session.prior_issue_question
        else (questions[-1] if questions else "")
    )
    customer_workspace_hint = (
        questions[-1] if identity_clarification_pending and questions else ""
    )
    return TicketSnapshot(
        ticket_id=session.ticket_id,
        generation=session.customer_context_generation,
        status=str(ticket.get("status") or "unknown"),
        channel=_channel(ticket),
        locale=str(ticket.get("locale") or ""),
        latest_event_id=latest_id,
        latest_event_at=_required_event_time(latest.get("created_at")),
        questions=questions,
        active_question=active_question,
        customer_workspace_hint=customer_workspace_hint,
        customer_messages=_customer_message_events(customer_messages),
        form_identity=FormIdentity(
            email=form.get("email", ""),
            workspace_id=form.get("workspace_id", ""),
        ),
        requester_email=_requester_email(ticket),
        attachments=_attachment_refs(customer_messages),
    )
