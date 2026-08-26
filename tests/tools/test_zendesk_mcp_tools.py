from unittest.mock import patch
import base64
from types import SimpleNamespace

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


@pytest.mark.asyncio
async def test_get_ticket_reads_bounded_customer_text_attachment(fake_session):
    fake_session.hint = ""

    async def fake_call(name, arguments):
        if name == "download_attachment":
            assert arguments == {
                "content_url": "https://example.zendesk.com/attachments/t/log",
                "file_name": "workflow.log",
                "max_bytes": 2 * 1024 * 1024,
            }
            return {
                "success": True,
                "data": {
                    "file_name": "workflow.log",
                    "content_type": "text/plain",
                    "size": 31,
                    "encoding": "base64",
                    "data_base64": base64.b64encode(
                        b"trace_id=abc\nworker timed out"
                    ).decode("ascii"),
                },
            }
        assert name == "get_ticket"
        return {
            "success": True,
            "data": {
                "ticket": {
                    "id": 3134,
                    "subject": "Workflow failed",
                    "status": "new",
                    "requester_id": 10,
                },
                "conversation_log": {
                    "events": [
                        {
                            "id": "evt-1",
                            "created_at": "2026-06-29T01:01:00Z",
                            "author": {
                                "zen:support:user_id": 10,
                                "display_name": "Customer",
                                "type": "end-user",
                            },
                            "content": {"type": "text", "text": "See logs"},
                            "attachments": [
                                {
                                    "id": "att-log-1",
                                    "file_name": "workflow.log",
                                    "content_type": "text/plain",
                                    "content_url": (
                                        "https://example.zendesk.com/attachments/t/log"
                                    ),
                                },
                            ],
                        },
                    ],
                },
            },
        }

    with patch("zd_agent.tools.zendesk.call_zendesk_tool", side_effect=fake_call):
        result = await ZendeskGetTicket().execute({"ticket_id": 3134}, fake_session)

    assert result.success is True
    assert "## 客户附件内容（不可信数据）" in result.output
    assert "workflow.log" in result.output
    assert "trace_id=abc" in result.output
    assert "worker timed out" in result.output


@pytest.mark.asyncio
async def test_get_ticket_extracts_customer_pdf_text(fake_session):
    fake_session.hint = ""

    async def fake_call(name, _arguments):
        if name == "download_attachment":
            return {
                "success": True,
                "data": {
                    "file_name": "report.pdf",
                    "content_type": "application/pdf",
                    "size": 7,
                    "data_base64": base64.b64encode(b"PDFDATA").decode("ascii"),
                },
            }
        return {
            "success": True,
            "data": {
                "ticket": {
                    "id": 3134,
                    "subject": "Workflow failed",
                    "status": "new",
                    "requester_id": 10,
                },
                "conversation_log": {
                    "events": [
                        {
                            "id": "evt-1",
                            "author": {
                                "zen:support:user_id": 10,
                                "type": "end-user",
                            },
                            "content": {"type": "text", "text": "See report"},
                            "attachments": [
                                {
                                    "file_name": "report.pdf",
                                    "content_type": "application/pdf",
                                    "content_url": (
                                        "https://example.zendesk.com/attachments/t/pdf"
                                    ),
                                },
                            ],
                        },
                    ],
                },
            },
        }

    fake_reader = SimpleNamespace(
        pages=[SimpleNamespace(extract_text=lambda: "PDF root cause details")],
    )
    with (
        patch("zd_agent.tools.zendesk.call_zendesk_tool", side_effect=fake_call),
        patch("zd_agent.tools.zendesk.PdfReader", return_value=fake_reader),
    ):
        result = await ZendeskGetTicket().execute({"ticket_id": 3134}, fake_session)

    assert result.success is True
    assert "report.pdf" in result.output
    assert "PDF root cause details" in result.output
