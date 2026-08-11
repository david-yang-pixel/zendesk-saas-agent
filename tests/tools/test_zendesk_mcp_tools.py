from unittest.mock import patch

import pytest

from zd_agent.tools.zendesk import ZendeskGetTicket


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
                            "author": {
                                "display_name": "Carlos",
                                "type": "end-user",
                            },
                            "content": {
                                "type": "text",
                                "text": "Please reclaim storage.",
                            },
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
