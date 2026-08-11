from __future__ import annotations

from typing import Any

import pytest

from zd_agent.casework.adapters.ticket import ZendeskCurrentTicketLoader


@pytest.mark.asyncio
async def test_current_ticket_loader_resolves_requester_email_from_requester_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fetch(_ticket_id: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        return ([{"id": "m1"}], {"id": 4321, "requester_id": 42})

    calls: list[tuple[str, dict[str, Any]]] = []

    async def call(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        calls.append((name, arguments))
        return {
            "success": True,
            "data": {"user": {"id": 42, "email": "Owner@Example.Test"}},
        }

    monkeypatch.setattr(
        "zd_agent.services.ticket_messages.fetch_current_customer_messages",
        fetch,
    )
    monkeypatch.setattr("zd_agent.integrations.zendesk_mcp.call_zendesk_tool", call)

    messages, ticket = await ZendeskCurrentTicketLoader().load(4321)

    assert messages == [{"id": "m1"}]
    assert ticket["requester"]["email"] == "Owner@Example.Test"
    assert calls == [("get_user", {"user_id": 42})]


@pytest.mark.asyncio
async def test_current_ticket_loader_retries_internally_when_requester_lookup_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fetch(_ticket_id: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        return ([{"id": "m1"}], {"id": 4321, "requester_id": 42})

    async def call(_name: str, _arguments: dict[str, Any]) -> dict[str, Any]:
        return {"success": False, "code": "unavailable"}

    monkeypatch.setattr(
        "zd_agent.services.ticket_messages.fetch_current_customer_messages",
        fetch,
    )
    monkeypatch.setattr("zd_agent.integrations.zendesk_mcp.call_zendesk_tool", call)

    with pytest.raises(RuntimeError, match="requester lookup"):
        await ZendeskCurrentTicketLoader().load(4321)


@pytest.mark.asyncio
async def test_current_ticket_loader_allows_form_email_when_requester_has_no_email(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    messages = [
        {
            "id": "m1",
            "form_data": {"email": "form-owner@example.test"},
        }
    ]

    async def fetch(_ticket_id: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        return (messages, {"id": 4321, "requester_id": 42})

    async def call(_name: str, _arguments: dict[str, Any]) -> dict[str, Any]:
        return {"success": True, "data": {"user": {"id": 42, "name": "Requester"}}}

    monkeypatch.setattr(
        "zd_agent.services.ticket_messages.fetch_current_customer_messages",
        fetch,
    )
    monkeypatch.setattr("zd_agent.integrations.zendesk_mcp.call_zendesk_tool", call)

    loaded_messages, ticket = await ZendeskCurrentTicketLoader().load(4321)

    assert loaded_messages == messages
    assert "requester" not in ticket
