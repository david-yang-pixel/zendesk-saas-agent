from unittest.mock import AsyncMock, patch

import pytest

from zd_agent.tools.zendesk import (
    ZendeskAddTags,
    ZendeskFollowTicket,
    ZendeskGetTicket,
    ZendeskInternalReply,
)


@pytest.mark.asyncio
async def test_get_ticket_uses_zendesk_mcp_conversation_log(fake_session):
    async def fake_call(name, arguments):
        assert name == "get_ticket"
        assert arguments == {
            "ticket_id": 3134,
            "include_comments": True,
            "include_conversation_log": True,
            "conversation_page_size": 100,
            "force_refresh": True,
        }
        return {
            "success": True,
            "data": {
                "ticket": {
                    "id": 3134,
                    "subject": "Storage mismatch",
                    "status": "new",
                    "priority": "normal",
                    "created_at": "2026-06-29T01:00:00Z",
                    "updated_at": "2026-06-29T02:00:00Z",
                    "tags": ["saas"],
                    "requester_id": 10,
                    "assignee_id": 20,
                    "organization_id": 30,
                    "description": "Customer says storage is wrong.",
                    "custom_fields": [{"id": 123, "value": "Production"}],
                },
                "conversation_log": {
                    "events": [
                        {
                            "id": "evt-1",
                            "created_at": "2026-06-29T01:01:00Z",
                            "author": {"display_name": "Carlos", "type": "end-user"},
                            "content": {"type": "text", "text": "Please reclaim storage."},
                        }
                    ]
                },
            },
        }

    with patch("zd_agent.tools.zendesk.call_zendesk_tool", side_effect=fake_call):
        result = await ZendeskGetTicket().execute({"ticket_id": 3134}, fake_session)

    assert result.success is True
    assert "# 工单 #3134" in result.output
    assert "Storage mismatch" in result.output
    assert "Please reclaim storage." in result.output
    assert fake_session.context.ticket_id == 3134


@pytest.mark.asyncio
async def test_internal_reply_uses_zendesk_mcp_after_local_guards(fake_session):
    fake_session.dry_run = False
    fake_session.reply_sent = False
    body = (
        "## Suggested Reply\n\nHi, we need human review.\n\n"
        "## Internal Analysis\n\nChecked logs.\n"
    )
    args = {
        "ticket_id": 3134,
        "body": body,
        "summary": {
            "problem_type": "platform_issue",
            "confidence": "low",
            "confidence_reason": "needs SRE",
            "customer_language": "English",
            "key_finding": "needs SRE",
            "needs_human": True,
        },
    }
    mock_call = AsyncMock(return_value={"success": True, "audit_id": "audit-1"})

    with patch("zd_agent.tools.zendesk.call_zendesk_tool", mock_call):
        result = await ZendeskInternalReply().execute(args, fake_session)

    assert result.success is True
    mock_call.assert_awaited_once()
    name, payload = mock_call.await_args.args
    assert name == "add_internal_note"
    assert payload["ticket_id"] == 3134
    assert payload["dry_run"] is False
    assert payload["confirm"] is True
    assert payload["explicit_user_internal_note_request"] == "agent internal escalation"
    assert "AI-generated reply for reference only" in payload["body"]


@pytest.mark.asyncio
async def test_add_tags_uses_zendesk_mcp_metadata_update(fake_session):
    fake_session.dry_run = False
    mock_call = AsyncMock(return_value={"success": True, "after": {"tags": ["bug", "helm"]}})

    with patch("zd_agent.tools.zendesk.call_zendesk_tool", mock_call):
        result = await ZendeskAddTags().execute(
            {"ticket_id": 3134, "tags": ["Bug", "helm", "bug"]},
            fake_session,
        )

    assert result.success is True
    name, payload = mock_call.await_args.args
    assert name == "update_ticket_metadata"
    assert payload["ticket_id"] == 3134
    assert payload["add_tags"] == ["bug", "helm"]
    assert payload["dry_run"] is False
    assert payload["confirm"] is True


@pytest.mark.asyncio
async def test_follow_ticket_uses_zendesk_mcp(fake_session):
    fake_session.dry_run = False
    mock_call = AsyncMock(return_value={"success": True, "audit_id": "audit-2"})

    with patch("zd_agent.tools.zendesk.call_zendesk_tool", mock_call):
        result = await ZendeskFollowTicket().execute({"ticket_id": 3134}, fake_session)

    assert result.success is True
    name, payload = mock_call.await_args.args
    assert name == "follow_ticket"
    assert payload["ticket_id"] == 3134
    assert payload["dry_run"] is False
    assert payload["confirm"] is True
    assert payload["explicit_user_follow_request"] == "agent follow ticket"
