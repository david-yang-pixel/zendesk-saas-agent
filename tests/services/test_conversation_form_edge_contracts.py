"""Regression contracts for paginated Messaging and Answer Bot form safety."""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from zd_agent.services.ticket_message_parsing import (
    _messaging_customer_messages,
    conversation_has_pending_form,
    extract_form_data_from_msgs,
)
from zd_agent.services.ticket_messages import _fetch_customer_messages
from zd_agent.services.verdict_classification import classify_ticket_messages
from zd_agent.session.agent_session import AgentSession
from zd_agent.tools.zendesk import ZendeskGetTicket, _extract_reply_language_hint
from zd_agent.trigger.decider import PreCheckVerdict


_TICKET_ID = 3461
_REQUESTER_ID = 49810567532436


def _text_event(message_id: str, text: str) -> dict[str, Any]:
    return {
        "id": message_id,
        "created_at": f"2026-07-16T04:1{message_id[-1]}:00Z",
        "author": {
            "type": "end-user",
            "zen:support:user_id": _REQUESTER_ID,
        },
        "content": {"type": "text", "text": text},
        "type": "Messaging::ConversationMessage",
    }


def _ticket_page(
    events: list[dict[str, Any]],
    *,
    has_more: bool,
    after_cursor: str | None,
) -> dict[str, Any]:
    return {
        "success": True,
        "data": {
            "ticket": {
                "id": _TICKET_ID,
                "requester_id": _REQUESTER_ID,
                "via": {"channel": "native_messaging"},
            },
            "comments": [],
            "conversation_log": {
                "events": events,
                "meta": {
                    "has_more": has_more,
                    "after_cursor": after_cursor,
                },
            },
        },
    }


def _conversation_page(
    events: list[dict[str, Any]],
    *,
    has_more: bool,
    after_cursor: str | None,
) -> dict[str, Any]:
    return {
        "success": True,
        "data": {
            "events": events,
            "meta": {
                "has_more": has_more,
                "after_cursor": after_cursor,
            },
        },
    }


@pytest.mark.asyncio
async def test_fetch_customer_messages_follows_all_conversation_log_cursors() -> None:
    """A form on a later page must be visible to readiness and classification."""
    calls: list[tuple[str, dict[str, Any]]] = []

    async def fake_call(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        calls.append((name, arguments))
        if name == "get_ticket":
            return _ticket_page(
                [_text_event("m1", "first page")],
                has_more=True,
                after_cursor="cursor-1",
            )
        if arguments.get("conversation_after") == "cursor-1":
            return _conversation_page(
                [_text_event("m2", "second page")],
                has_more=True,
                after_cursor="cursor-2",
            )
        if arguments.get("conversation_after") == "cursor-2":
            return _conversation_page(
                [_text_event("m3", "final page")],
                has_more=False,
                after_cursor="cursor-3",
            )
        raise AssertionError(f"unexpected Zendesk MCP call: {name} {arguments}")

    with patch(
        "zd_agent.integrations.zendesk_mcp.call_zendesk_tool",
        side_effect=fake_call,
    ):
        messages, ticket = await _fetch_customer_messages(
            _TICKET_ID,
            log_success=False,
        )

    assert [message["id"] for message in messages] == ["m1", "m2", "m3"]
    assert ticket is not None
    assert [
        event["id"] for event in ticket["_zd_agent_conversation_events"]
    ] == ["m1", "m2", "m3"]
    assert [name for name, _ in calls] == [
        "get_ticket",
        "get_ticket_conversation_log",
        "get_ticket_conversation_log",
    ]
    assert [
        arguments.get("conversation_after") for _, arguments in calls[1:]
    ] == ["cursor-1", "cursor-2"]


@pytest.mark.asyncio
async def test_conversation_pagination_stops_when_after_cursor_repeats() -> None:
    """A malformed upstream cursor must fail closed, not expose a partial log."""
    calls: list[tuple[str, dict[str, Any]]] = []

    async def fake_call(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        calls.append((name, arguments))
        if len(calls) > 2:
            raise AssertionError("repeated after_cursor must terminate pagination")
        if name == "get_ticket":
            return _ticket_page(
                [_text_event("m1", "first page")],
                has_more=True,
                after_cursor="cursor-loop",
            )
        assert name == "get_ticket_conversation_log"
        assert arguments.get("conversation_after") == "cursor-loop"
        return _conversation_page(
            [_text_event("m2", "second page")],
            has_more=True,
            after_cursor="cursor-loop",
        )

    with patch(
        "zd_agent.integrations.zendesk_mcp.call_zendesk_tool",
        side_effect=fake_call,
    ):
        messages, ticket = await _fetch_customer_messages(
            _TICKET_ID,
            log_success=False,
        )

    assert messages == []
    assert ticket is None
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_conversation_pagination_fails_closed_without_after_cursor() -> None:
    """``has_more`` without a usable cursor is an incomplete snapshot."""
    call = AsyncMock(return_value=_ticket_page(
        [_text_event("m1", "first page")],
        has_more=True,
        after_cursor=None,
    ))

    with patch(
        "zd_agent.integrations.zendesk_mcp.call_zendesk_tool",
        call,
    ):
        messages, ticket = await _fetch_customer_messages(
            _TICKET_ID,
            log_success=False,
        )

    assert messages == []
    assert ticket is None
    call.assert_awaited_once()


@pytest.mark.asyncio
async def test_conversation_pagination_fails_closed_at_ten_page_cap() -> None:
    """The safety cap must defer instead of treating ten partial pages as complete."""
    calls = 0

    async def fake_call(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        if name == "get_ticket":
            return _ticket_page(
                [_text_event("m0", "page 0")],
                has_more=True,
                after_cursor="cursor-1",
            )
        cursor_number = int(str(arguments["conversation_after"]).rsplit("-", 1)[1])
        return _conversation_page(
            [_text_event(f"m{cursor_number}", f"page {cursor_number}")],
            has_more=True,
            after_cursor=f"cursor-{cursor_number + 1}",
        )

    with patch(
        "zd_agent.integrations.zendesk_mcp.call_zendesk_tool",
        side_effect=fake_call,
    ):
        messages, ticket = await _fetch_customer_messages(
            _TICKET_ID,
            log_success=False,
        )

    assert calls == 10
    assert messages == []
    assert ticket is None


@pytest.mark.asyncio
async def test_conversation_pagination_fails_closed_when_tenth_page_is_final() -> None:
    """Reaching the page cap is deferred even when page ten says it is final."""
    calls = 0

    async def fake_call(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        if name == "get_ticket":
            return _ticket_page(
                [_text_event("m0", "page 0")],
                has_more=True,
                after_cursor="cursor-1",
            )
        cursor_number = int(str(arguments["conversation_after"]).rsplit("-", 1)[1])
        return _conversation_page(
            [_text_event(f"m{cursor_number}", f"page {cursor_number}")],
            has_more=cursor_number < 9,
            after_cursor=f"cursor-{cursor_number + 1}",
        )

    with patch(
        "zd_agent.integrations.zendesk_mcp.call_zendesk_tool",
        side_effect=fake_call,
    ):
        messages, ticket = await _fetch_customer_messages(
            _TICKET_ID,
            log_success=False,
        )

    assert calls == 10
    assert messages == []
    assert ticket is None


@pytest.mark.asyncio
async def test_conversation_pagination_fails_closed_above_event_cap() -> None:
    """A response larger than the event budget cannot be silently truncated."""
    events = [_text_event(f"m{index}", f"message {index}") for index in range(1001)]
    call = AsyncMock(return_value=_ticket_page(
        events,
        has_more=False,
        after_cursor=None,
    ))

    with patch(
        "zd_agent.integrations.zendesk_mcp.call_zendesk_tool",
        call,
    ):
        messages, ticket = await _fetch_customer_messages(
            _TICKET_ID,
            log_success=False,
        )

    assert messages == []
    assert ticket is None


@pytest.mark.asyncio
async def test_conversation_pagination_fails_closed_at_exact_event_cap() -> None:
    """Reaching exactly 1,000 events uses the same conservative defer path."""
    events = [_text_event(f"m{index}", f"message {index}") for index in range(1000)]
    call = AsyncMock(return_value=_ticket_page(
        events,
        has_more=False,
        after_cursor=None,
    ))

    with patch(
        "zd_agent.integrations.zendesk_mcp.call_zendesk_tool",
        call,
    ):
        messages, ticket = await _fetch_customer_messages(
            _TICKET_ID,
            log_success=False,
        )

    assert messages == []
    assert ticket is None


@pytest.mark.asyncio
async def test_conversation_pagination_fails_closed_at_event_cap_with_more() -> None:
    """Exactly 1,000 events plus ``has_more`` is already known incomplete."""
    events = [_text_event(f"m{index}", f"message {index}") for index in range(1000)]
    call = AsyncMock(return_value=_ticket_page(
        events,
        has_more=True,
        after_cursor="cursor-over-cap",
    ))

    with patch(
        "zd_agent.integrations.zendesk_mcp.call_zendesk_tool",
        call,
    ):
        messages, ticket = await _fetch_customer_messages(
            _TICKET_ID,
            log_success=False,
        )

    assert messages == []
    assert ticket is None
    call.assert_awaited_once()


@pytest.mark.asyncio
async def test_agent_get_ticket_uses_the_same_complete_conversation_loader() -> None:
    """The diagnostic Agent must see later conversation pages too."""
    calls: list[tuple[str, dict[str, Any]]] = []

    async def fake_call(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        calls.append((name, arguments))
        if name == "get_ticket":
            return _ticket_page(
                [_text_event("m1", "first page")],
                has_more=True,
                after_cursor="cursor-1",
            )
        return _conversation_page(
            [_text_event("m2", "second page")],
            has_more=False,
            after_cursor="cursor-2",
        )

    session = AgentSession(ticket_id=_TICKET_ID)
    with patch("zd_agent.tools.zendesk.call_zendesk_tool", side_effect=fake_call):
        result = await ZendeskGetTicket().execute(
            {"ticket_id": _TICKET_ID},
            session,
        )

    assert result.success is True
    assert "first page" in result.output
    assert "second page" in result.output
    assert [name for name, _ in calls] == [
        "get_ticket",
        "get_ticket_conversation_log",
    ]


def _form_event(
    message_id: str,
    *,
    content_type: str,
    field_names: list[str],
) -> dict[str, Any]:
    fields = [
        {
            "type": "TEXT",
            "name": field_name,
            "label": field_name.rsplit(".", 1)[-1],
            **({"text": f"value-{index}"} if content_type == "formResponse" else {}),
        }
        for index, field_name in enumerate(field_names)
    ]
    content: dict[str, Any] = {
        "type": content_type,
        "fields": fields,
    }
    if content_type == "form":
        content.update({"block_chat_input": True, "submitted": False})
    return {
        "id": message_id,
        "created_at": "2026-07-16T04:15:24Z",
        "author": {
            "type": "system" if content_type == "form" else "end-user",
        },
        "content": content,
        "type": "Messaging::ConversationMessage",
    }


def test_unrelated_form_response_does_not_release_blocking_profile_form() -> None:
    """Only a response with the pending form's fields can satisfy that form."""
    profile_fields = [
        "dataCapture.systemField.requester.name",
        "dataCapture.systemField.requester.email",
        "dataCapture.ticketField.41021162892948",
    ]
    events = [
        _form_event("profile-form", content_type="form", field_names=profile_fields),
        _form_event(
            "other-response",
            content_type="formResponse",
            field_names=["dataCapture.ticketField.999999"],
        ),
    ]

    assert conversation_has_pending_form(events) is True


def test_corresponding_form_response_releases_blocking_profile_form() -> None:
    """A later response carrying the same field signature satisfies the form."""
    profile_fields = [
        "dataCapture.systemField.requester.name",
        "dataCapture.systemField.requester.email",
        "dataCapture.ticketField.41021162892948",
    ]
    events = [
        _form_event("profile-form", content_type="form", field_names=profile_fields),
        _form_event(
            "profile-response",
            content_type="formResponse",
            field_names=profile_fields,
        ),
    ]

    assert conversation_has_pending_form(events) is False


def test_reissued_blocking_form_is_replaced_by_latest_copy() -> None:
    """A repeated bot form must not leave an unreachable historical wait."""
    profile_fields = [
        "dataCapture.systemField.requester.name",
        "dataCapture.systemField.requester.email",
        "dataCapture.ticketField.41021162892948",
    ]
    events = [
        _form_event("profile-form-1", content_type="form", field_names=profile_fields),
        _form_event("profile-form-2", content_type="form", field_names=profile_fields),
        _form_event(
            "profile-response",
            content_type="formResponse",
            field_names=profile_fields,
        ),
    ]

    assert conversation_has_pending_form(events) is False


def test_partial_repeated_profile_forms_merge_latest_value_per_field() -> None:
    """A partial resubmission updates supplied fields without erasing prior ones."""
    messages = [
        {
            "content_type": "formResponse",
            "form_data": {
                "name": "Ellie",
                "email": "old@example.test",
                "workspace_id": "workspace-original",
            },
        },
        {
            "content_type": "formResponse",
            "form_data": {"email": "new@example.test"},
        },
        {
            "content_type": "formResponse",
            "form_data": {"workspace_id": "workspace-current"},
        },
    ]

    assert extract_form_data_from_msgs(messages) == {
        "name": "Ellie",
        "email": "new@example.test",
        "workspace_id": "workspace-current",
    }


@pytest.mark.asyncio
async def test_custom_form_response_issue_field_remains_in_classification() -> None:
    """Profile capture is context, but a custom issue field is customer issue text."""
    event = {
        "id": "issue-form-response",
        "created_at": "2026-07-16T04:18:53Z",
        "author": {
            "type": "end-user",
            "zen:support:user_id": _REQUESTER_ID,
        },
        "content": {
            "type": "formResponse",
            "fields": [
                {
                    "type": "TEXT",
                    "name": "dataCapture.systemField.requester.name",
                    "label": "Name",
                    "text": "Ellie",
                },
                {
                    "type": "EMAIL",
                    "name": "dataCapture.systemField.requester.email",
                    "label": "Email",
                    "email": "ellie@example.test",
                },
                {
                    "type": "TEXT",
                    "name": "dataCapture.ticketField.999999",
                    "label": "Issue details",
                    "text": "The workflow remains Running and never completes.",
                },
            ],
        },
    }
    [message] = _messaging_customer_messages([event])
    classify = AsyncMock(return_value=PreCheckVerdict(
        verdict="substantial_issue",
        completeness=90,
        reason="The custom field contains an actionable problem.",
    ))

    await classify_ticket_messages(
        ticket_id=_TICKET_ID,
        customer_msgs=[message],
        model="gpt-test",
        api_key="sk-test",
        classify_messages_fn=classify,
    )

    classified_text = "\n".join(classify.await_args.kwargs["messages"])
    assert "workflow remains Running" in classified_text
    assert "ellie@example.test" not in classified_text


@pytest.mark.asyncio
async def test_all_requester_system_fields_are_excluded_from_classification() -> None:
    """Unknown requester PII fields must not become customer issue text."""
    event = {
        "id": "profile-form-response",
        "created_at": "2026-07-16T04:18:53Z",
        "author": {"type": "end-user"},
        "content": {
            "type": "formResponse",
            "fields": [
                {
                    "type": "TEXT",
                    "name": "dataCapture.systemField.requester.phone",
                    "label": "Phone",
                    "text": "+86-138-0000-0000",
                },
                {
                    "type": "TEXT",
                    "name": "dataCapture.ticketField.999999",
                    "label": "Issue details",
                    "text": "The workflow never completes.",
                },
            ],
        },
    }
    [message] = _messaging_customer_messages([event])
    classify = AsyncMock(return_value=PreCheckVerdict(
        verdict="substantial_issue",
        completeness=90,
        reason="actionable issue",
    ))

    await classify_ticket_messages(
        ticket_id=_TICKET_ID,
        customer_msgs=[message],
        model="gpt-test",
        api_key="sk-test",
        classify_messages_fn=classify,
    )

    classified_text = "\n".join(classify.await_args.kwargs["messages"])
    assert "workflow never completes" in classified_text
    assert "+86-138" not in classified_text


def test_language_hint_requires_explicit_messaging_event_type() -> None:
    """Generic user-shaped rows must not override a real Messaging message."""
    hint = _extract_reply_language_hint(
        thread_type="conversation_log",
        thread_items=[
            {
                "type": "Messaging::ConversationMessage",
                "author": {"type": "end-user"},
                "content": {"type": "text", "text": "日本語で確認をお願いします。"},
            },
            {
                "author": {"type": "user"},
                "content": {"type": "text", "text": "This synthetic row is English."},
            },
        ],
        ticket_description="",
    )

    assert hint is not None
    assert hint["language"] == "ja"


def test_language_hint_ignores_non_public_synthetic_comment_event() -> None:
    """The private ticket-description Comment is not a customer language signal."""
    real_customer_message = {
        "id": "customer-message",
        "created_at": "2026-07-16T04:15:21Z",
        "author": {"type": "end-user"},
        "content": {
            "type": "text",
            "text": "既存のナレッジ文書を表示できません。確認をお願いします。",
        },
        "type": "Messaging::ConversationMessage",
    }
    synthetic_private_comment = {
        "id": "ticket-description-comment",
        "created_at": "2026-07-16T04:15:22Z",
        "author": {"type": "user"},
        "content": {
            "type": "html",
            "body": "<p>Conversation with Ellie</p>",
        },
        "type": "Comment",
        "metadata": {"is_public": False},
    }

    hint = _extract_reply_language_hint(
        thread_type="conversation_log",
        thread_items=[real_customer_message, synthetic_private_comment],
        ticket_description="",
    )

    assert hint is not None
    assert hint["language"] == "ja"
    assert "ナレッジ" in hint["evidence"]
