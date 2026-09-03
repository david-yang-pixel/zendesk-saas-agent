from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, cast

import httpx
import pytest
from pydantic import JsonValue

from casework_agent_core.domain import TicketSnapshotNotReadyError
from casework_sandbox.zendesk import (
    ZendeskApiReader,
    ZendeskMessagingLoader,
    ZendeskSnapshotError,
    canonical_messaging_event_id,
)
from casework_tool_sdk.registry import ToolExecutionContext
from contracts.ticket import TicketMessage
from contracts.tool import ToolResult, ToolResultStatus


def _response(payload: object, status_code: int = 200) -> httpx.Response:
    return httpx.Response(
        status_code,
        content=json.dumps(payload).encode(),
        headers={"content-type": "application/json"},
    )


class _FakeStructuredTool:
    def __init__(self, payloads: list[dict[str, JsonValue]]) -> None:
        self.payloads = payloads
        self.calls: list[dict[str, Any]] = []

    async def __call__(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        assert context.ticket_id == 3726
        self.calls.append(arguments)
        payload: JsonValue = self.payloads.pop(0)
        return ToolResult(
            status=ToolResultStatus.SUCCESS,
            summary="Zendesk snapshot loaded",
            data={"structured_content": payload},
            evidence_id=f"evidence-{len(self.calls)}",
            observed_at=datetime.now(UTC),
        )


class _SequenceMessagingLoader:
    def __init__(self, snapshots: list[tuple[TicketMessage, ...]]) -> None:
        self.snapshots = snapshots
        self.calls = 0

    async def fetch(self, ticket_id: int) -> tuple[TicketMessage, ...]:
        assert ticket_id == 3726
        self.calls += 1
        return self.snapshots.pop(0)


def _messaging_message(event_id: str, *, minute: int) -> TicketMessage:
    return TicketMessage(
        event_id=f"messaging:{event_id}",
        author_origin="customer",
        public=True,
        body=f"message {event_id}",
        created_at=datetime(2026, 9, 2, 8, minute, tzinfo=UTC),
    )


def _ticket_transport() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v2/tickets/3726.json":
            return _response(
                {
                    "ticket": {
                        "id": 3726,
                        "status": "open",
                        "requester_id": 10,
                        "assignee_id": 20,
                        "tags": ["saas"],
                        "custom_fields": [],
                    }
                }
            )
        if request.url.path == "/api/v2/tickets/3726/comments.json":
            return _response({"comments": [], "next_page": None})
        if request.url.path == "/api/v2/users/10.json":
            return _response({"user": {"id": 10, "locale": "zh-CN"}})
        return _response({"error": "not found"}, status_code=404)

    return httpx.MockTransport(handler)


def test_messaging_event_id_normalization_is_idempotent() -> None:
    assert canonical_messaging_event_id("evt-1") == "messaging:evt-1"
    assert canonical_messaging_event_id("messaging:evt-1") == "messaging:evt-1"
    assert canonical_messaging_event_id("102") == "102"


@pytest.mark.asyncio
async def test_reader_waits_until_triggering_messaging_event_is_visible() -> None:
    old = _messaging_message("evt-old", minute=1)
    new = _messaging_message("evt-new", minute=2)
    loader = _SequenceMessagingLoader([(old,), (old, new)])
    sleeps: list[float] = []

    async def sleep(delay: float) -> None:
        sleeps.append(delay)

    async with httpx.AsyncClient(
        base_url="https://tenant.zendesk.com",
        transport=_ticket_transport(),
    ) as client:
        snapshot = await ZendeskApiReader(
            client=client,
            base_url="https://tenant.zendesk.com",
            email="support@example.invalid",
            api_token="test-token",
            messaging_loader=cast(ZendeskMessagingLoader, loader),
            snapshot_retry_delays=(0.25, 0.75),
            sleep=sleep,
        ).fetch_ticket(3726, expected_event_id="evt-new")

    assert snapshot.latest_customer_cursor == "messaging:evt-new"
    assert loader.calls == 2
    assert sleeps == [0.25]


@pytest.mark.asyncio
async def test_reader_accepts_webhook_ulid_when_latest_customer_message_is_current(
) -> None:
    # Zendesk's webhook event ID and Messaging conversation event ID are
    # different identifiers. Their timestamps describe the same customer turn.
    latest = TicketMessage(
        event_id="messaging:01M1GRS7C956CAXQNAFNG7M21A",
        author_origin="customer",
        public=True,
        body="Latest customer message",
        created_at=datetime(2026, 9, 2, 9, 56, 40, tzinfo=UTC),
    )
    loader = _SequenceMessagingLoader([(latest,)])

    async with httpx.AsyncClient(
        base_url="https://tenant.zendesk.com",
        transport=_ticket_transport(),
    ) as client:
        snapshot = await ZendeskApiReader(
            client=client,
            base_url="https://tenant.zendesk.com",
            email="support@example.invalid",
            api_token="test-token",
            messaging_loader=cast(ZendeskMessagingLoader, loader),
            snapshot_retry_delays=(),
        ).fetch_ticket(
            3726,
            expected_event_id="messaging:01M1GRS82R7YP4SHJM831AXTE1",
        )

    assert snapshot.latest_customer_cursor == (
        "messaging:01M1GRS7C956CAXQNAFNG7M21A"
    )
    assert loader.calls == 1


@pytest.mark.asyncio
async def test_reader_raises_typed_retryable_error_when_trigger_never_appears() -> None:
    old = _messaging_message("evt-old", minute=1)
    loader = _SequenceMessagingLoader([(old,), (old,), (old,)])
    sleeps: list[float] = []

    async def sleep(delay: float) -> None:
        sleeps.append(delay)

    async with httpx.AsyncClient(
        base_url="https://tenant.zendesk.com",
        transport=_ticket_transport(),
    ) as client:
        reader = ZendeskApiReader(
            client=client,
            base_url="https://tenant.zendesk.com",
            email="support@example.invalid",
            api_token="test-token",
            messaging_loader=cast(ZendeskMessagingLoader, loader),
            snapshot_retry_delays=(0.25, 0.75),
            sleep=sleep,
        )
        with pytest.raises(TicketSnapshotNotReadyError) as captured:
            await reader.fetch_ticket(3726, expected_event_id="evt-new")

    assert captured.value.expected_event_id == "messaging:evt-new"
    assert captured.value.observed_cursor == "messaging:evt-old"
    assert loader.calls == 3
    assert sleeps == [0.25, 0.75]


@pytest.mark.asyncio
async def test_reader_fetches_ticket_all_comment_pages_attachments_and_locale() -> None:
    seen_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_paths.append(str(request.url))
        assert request.headers["authorization"].startswith("Basic ")
        path = request.url.path
        if path == "/api/v2/tickets/3726.json":
            return _response(
                {
                    "ticket": {
                        "id": 3726,
                        "status": "open",
                        "requester_id": 10,
                        "assignee_id": 20,
                        "tags": ["saas"],
                        "custom_fields": [{"id": 42, "value": "production"}],
                    }
                }
            )
        if path == "/api/v2/tickets/3726/comments.json" and not request.url.params:
            return _response(
                {
                    "comments": [
                        {
                            "id": 100,
                            "author_id": 10,
                            "public": True,
                            "body": "Original question",
                            "created_at": "2026-09-01T00:00:00Z",
                            "attachments": [
                                {
                                    "id": 700,
                                    "file_name": "error.txt",
                                    "content_type": "text/plain",
                                    "size": 12,
                                }
                            ],
                        }
                    ],
                    "next_page": (
                        "https://tenant.zendesk.com/api/v2/tickets/3726/"
                        "comments.json?page=2"
                    ),
                }
            )
        if path == "/api/v2/tickets/3726/comments.json":
            return _response(
                {
                    "comments": [
                        {
                            "id": 101,
                            "author_id": 20,
                            "public": False,
                            "body": "Internal investigation",
                            "created_at": "2026-09-01T00:01:00Z",
                            "attachments": cast(list[object], []),
                        },
                        {
                            "id": 102,
                            "author_id": 10,
                            "public": True,
                            "body": "Clarification",
                            "created_at": "2026-09-01T00:02:00Z",
                            "attachments": cast(list[object], []),
                        },
                    ],
                    "next_page": None,
                }
            )
        if path == "/api/v2/users/10.json":
            return _response({"user": {"id": 10, "locale": "ja-JP"}})
        return _response({"error": "not found"}, status_code=404)

    async with httpx.AsyncClient(
        base_url="https://tenant.zendesk.com",
        transport=httpx.MockTransport(handler),
    ) as client:
        snapshot = await ZendeskApiReader(
            client=client,
            base_url="https://tenant.zendesk.com",
            email="support@example.invalid",
            api_token="test-token",
        ).fetch_ticket(3726)

    assert snapshot.ticket_id == 3726
    assert snapshot.locale == "ja-JP"
    assert snapshot.latest_customer_cursor == "102"
    assert snapshot.form_fields == {"42": "production"}
    assert [message.event_id for message in snapshot.messages] == ["100", "101", "102"]
    assert [message.author_origin for message in snapshot.messages] == [
        "customer",
        "agent",
        "customer",
    ]
    assert snapshot.messages[0].attachments[0].filename == "error.txt"
    assert len(seen_paths) == 4


@pytest.mark.asyncio
async def test_reader_merges_complete_paginated_messaging_conversation() -> None:
    ticket_tool = _FakeStructuredTool(
        [
            {
                "success": True,
                "data": {
                    "conversation_log": {
                        "events": [
                            {
                                "id": "evt-1",
                                "created_at": "2026-09-01T00:01:00Z",
                                "author": {"type": "end-user"},
                                "content": {"type": "text", "text": "Initial issue"},
                            },
                            {
                                "id": "evt-agent",
                                "created_at": "2026-09-01T00:02:00Z",
                                "author": {"type": "agent"},
                                "metadata": {"is_public": False},
                                "content": {"type": "html", "body": "<b>Internal</b>"},
                            },
                        ],
                        "meta": {"has_more": True, "after_cursor": "cursor-1"},
                    }
                },
            }
        ]
    )
    conversation_tool = _FakeStructuredTool(
        [
            {
                "success": True,
                "data": {
                    "conversation_log": {
                        "events": [
                            {
                                "id": "evt-2",
                                "created_at": "2026-09-01T00:03:00Z",
                                "author": {"type": "end-user"},
                                "content": {
                                    "type": "text",
                                    "text": "Latest clarification",
                                },
                            }
                        ],
                        "meta": {"has_more": False},
                    }
                },
            }
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v2/tickets/3726.json":
            return _response(
                {
                    "ticket": {
                        "id": 3726,
                        "status": "open",
                        "requester_id": 10,
                        "assignee_id": 20,
                        "tags": ["saas"],
                        "custom_fields": [],
                    }
                }
            )
        if request.url.path == "/api/v2/tickets/3726/comments.json":
            return _response({"comments": [], "next_page": None})
        if request.url.path == "/api/v2/users/10.json":
            return _response({"user": {"id": 10, "locale": "zh-CN"}})
        return _response({"error": "not found"}, status_code=404)

    loader = ZendeskMessagingLoader(
        ticket_tool=ticket_tool,
        conversation_tool=conversation_tool,
    )
    async with httpx.AsyncClient(
        base_url="https://tenant.zendesk.com",
        transport=httpx.MockTransport(handler),
    ) as client:
        snapshot = await ZendeskApiReader(
            client=client,
            base_url="https://tenant.zendesk.com",
            email="support@example.invalid",
            api_token="test-token",
            messaging_loader=loader,
        ).fetch_ticket(3726)

    assert [message.event_id for message in snapshot.messages] == [
        "messaging:evt-1",
        "messaging:evt-agent",
        "messaging:evt-2",
    ]
    assert snapshot.messages[1].body == "Internal"
    assert snapshot.messages[1].public is False
    assert snapshot.latest_customer_cursor == "messaging:evt-2"
    assert conversation_tool.calls == [
        {
            "ticket_id": 3726,
            "conversation_after": "cursor-1",
            "conversation_page_size": 100,
            "force_refresh": True,
        }
    ]


@pytest.mark.asyncio
async def test_messaging_loader_fails_closed_on_repeated_cursor() -> None:
    page: dict[str, JsonValue] = {
        "success": True,
        "data": {
            "conversation_log": {
                "events": [],
                "meta": {"has_more": True, "after_cursor": "same"},
            }
        },
    }
    loader = ZendeskMessagingLoader(
        ticket_tool=_FakeStructuredTool([page]),
        conversation_tool=_FakeStructuredTool([page]),
    )

    with pytest.raises(ZendeskSnapshotError, match="cursor repeated"):
        await loader.fetch(3726)


@pytest.mark.asyncio
async def test_reader_rejects_cross_origin_pagination_before_sending_credentials() -> (
    None
):
    seen_hosts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_hosts.append(str(request.url.host))
        if request.url.path.endswith("3726.json"):
            return _response(
                {
                    "ticket": {
                        "id": 3726,
                        "status": "open",
                        "requester_id": 10,
                        "assignee_id": 20,
                        "tags": ["saas"],
                        "custom_fields": [],
                    }
                }
            )
        return _response(
            {
                "comments": cast(list[object], []),
                "next_page": "https://attacker.invalid/steal?page=2",
            }
        )

    async with httpx.AsyncClient(
        base_url="https://tenant.zendesk.com",
        transport=httpx.MockTransport(handler),
    ) as client:
        reader = ZendeskApiReader(
            client=client,
            base_url="https://tenant.zendesk.com",
            email="support@example.invalid",
            api_token="test-token",
        )
        with pytest.raises(ZendeskSnapshotError, match="cross-origin"):
            await reader.fetch_ticket(3726)

    assert seen_hosts == ["tenant.zendesk.com", "tenant.zendesk.com"]


@pytest.mark.asyncio
async def test_reader_merges_flat_page_and_extracts_sunco_reference() -> None:
    ticket_tool = _FakeStructuredTool(
        [
            {
                "success": True,
                "data": {
                    "conversation_log": {
                        "events": [
                            {
                                "id": "01M1FIRST",
                                "created_at": "2026-09-01T00:01:00Z",
                                "author": {"type": "end-user"},
                                "content": {"type": "text", "text": "Page 1 message"},
                            },
                        ],
                        "meta": {"has_more": True, "after_cursor": "cursor-page-2"},
                    }
                },
            }
        ]
    )
    conversation_tool = _FakeStructuredTool(
        [
            {
                "success": True,
                "data": {
                    "events": [
                        {
                            "id": "01M1SECOND",
                            "reference": (
                                "zen:sunco:conversation_message:6a97f2d85b554468b974cad5"
                            ),
                            "created_at": "2026-09-01T00:02:00Z",
                            "author": {"type": "end-user"},
                            "content": {
                                "type": "text",
                                "text": "Page 2 message with SunCo ref",
                            },
                        }
                    ],
                    "meta": {"has_more": False},
                },
            }
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v2/tickets/3726.json":
            return _response(
                {
                    "ticket": {
                        "id": 3726,
                        "status": "open",
                        "requester_id": 10,
                        "assignee_id": 20,
                        "tags": ["saas"],
                        "custom_fields": [],
                    }
                }
            )
        if request.url.path == "/api/v2/tickets/3726/comments.json":
            return _response({"comments": [], "next_page": None})
        if request.url.path == "/api/v2/users/10.json":
            return _response({"user": {"id": 10, "locale": "zh-CN"}})
        return _response({"error": "not found"}, status_code=404)

    loader = ZendeskMessagingLoader(
        ticket_tool=ticket_tool,
        conversation_tool=conversation_tool,
    )
    async with httpx.AsyncClient(
        base_url="https://tenant.zendesk.com",
        transport=httpx.MockTransport(handler),
    ) as client:
        reader = ZendeskApiReader(
            client=client,
            base_url="https://tenant.zendesk.com",
            email="support@example.invalid",
            api_token="test-token",
            messaging_loader=loader,
        )
        # Should match by SunCo message ID with prefix
        snapshot = await reader.fetch_ticket(
            3726, expected_event_id="messaging:6a97f2d85b554468b974cad5"
        )
        assert snapshot.latest_customer_cursor == "messaging:6a97f2d85b554468b974cad5"
        assert len(snapshot.messages) == 2
        sunco_msg = snapshot.messages[1]
        assert sunco_msg.event_id == "messaging:6a97f2d85b554468b974cad5"
        assert "messaging:01M1SECOND" in sunco_msg.alias_ids
        assert "6a97f2d85b554468b974cad5" in sunco_msg.alias_ids

        # Should also match by raw SunCo message ID without prefix
        assert reader._contains_event(snapshot, "6a97f2d85b554468b974cad5")  # pyright: ignore[reportPrivateUsage]
        # Should also match by ULID
        assert reader._contains_event(snapshot, "01M1SECOND")  # pyright: ignore[reportPrivateUsage]
        assert reader._contains_event(snapshot, "messaging:01M1SECOND")  # pyright: ignore[reportPrivateUsage]
