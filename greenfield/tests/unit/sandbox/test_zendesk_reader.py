from __future__ import annotations

import json
from typing import cast

import httpx
import pytest

from casework_sandbox.zendesk import ZendeskApiReader, ZendeskSnapshotError


def _response(payload: object, status_code: int = 200) -> httpx.Response:
    return httpx.Response(
        status_code,
        content=json.dumps(payload).encode(),
        headers={"content-type": "application/json"},
    )


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
async def test_reader_rejects_cross_origin_pagination_before_sending_credentials(
) -> None:
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
