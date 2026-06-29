from unittest.mock import patch

import pytest

from zd_agent.tools.zendesk import (
    ZendeskGetArticle,
    ZendeskGetAttachments,
    ZendeskGetTicket,
    ZendeskGetUser,
    ZendeskSearch,
    ZendeskSearchArticles,
)


async def _mcp_must_not_call(*_args, **_kwargs):
    raise AssertionError("Zendesk MCP should not be called for invalid args")


@pytest.mark.asyncio
async def test_get_ticket_rejects_invalid_ticket_id_before_client(fake_session):
    with patch("zd_agent.tools.zendesk.call_zendesk_tool", side_effect=_mcp_must_not_call):
        result = await ZendeskGetTicket().execute({"ticket_id": "12345"}, fake_session)

    assert result.success is False
    assert result.error == "ticket_id must be a positive integer"


@pytest.mark.asyncio
async def test_get_attachments_rejects_invalid_ticket_id_before_client(fake_session):
    with patch("zd_agent.tools.zendesk.call_zendesk_tool", side_effect=_mcp_must_not_call):
        result = await ZendeskGetAttachments().execute({"ticket_id": True}, fake_session)

    assert result.success is False
    assert result.error == "ticket_id must be a positive integer"


@pytest.mark.asyncio
async def test_get_attachments_downloads_inline_image_and_file_through_mcp(tmp_path, fake_session):
    fake_session.sandbox_dir = tmp_path
    events = [
        {
            "id": "evt-1",
            "created_at": "2026-06-27T10:11:18Z",
            "author": {"type": "end-user"},
            "content": {
                "type": "html",
                "body": (
                    '<p>Screenshot</p>'
                    '<img src="https://example.zendesk.com/sc/attachments/v2/a.png">'
                ),
            },
        },
    ]
    calls: list[tuple[str, dict]] = []

    async def fake_call(name, arguments):
        calls.append((name, arguments))
        if name == "list_attachments":
            assert arguments["ticket_id"] == 12345
            return {
                "success": True,
                "data": {
                    "attachments": [
                        {
                            "content_url": "https://example.zendesk.com/attachments/token/doc",
                            "file_name": "diagnostic.txt",
                            "content_type": "text/plain",
                        }
                    ]
                },
            }
        if name == "get_ticket":
            assert arguments["ticket_id"] == 12345
            return {
                "success": True,
                "data": {
                    "ticket": {"id": 12345, "via": {"channel": "native_messaging"}},
                    "conversation_log": {"events": events},
                },
            }
        if name == "download_attachment":
            if arguments["content_url"] == "https://example.zendesk.com/sc/attachments/v2/a.png":
                return {
                    "success": True,
                    "data": {
                        "file_name": "a.png",
                        "content_type": "image/png",
                        "data_base64": "UE5HREFUQQ==",
                    },
                }
            if arguments["content_url"] == "https://example.zendesk.com/attachments/token/doc":
                return {
                    "success": True,
                    "data": {
                        "file_name": "diagnostic.txt",
                        "content_type": "text/plain",
                        "data_base64": "VEVYVERBVEE=",
                    },
                }
        raise AssertionError(f"unexpected Zendesk MCP tool: {name}")

    with patch("zd_agent.tools.zendesk.call_zendesk_tool", side_effect=fake_call):
        result = await ZendeskGetAttachments().execute({"ticket_id": 12345}, fake_session)

    assert result.success is True
    assert "该工单没有附件或图片" not in result.output
    assert "已下载 2 个文件" in result.output
    assert (tmp_path / "attachments" / "a.png").read_bytes() == b"PNGDATA"
    assert (tmp_path / "attachments" / "diagnostic.txt").read_bytes() == b"TEXTDATA"
    assert [name for name, _ in calls].count("download_attachment") == 2


@pytest.mark.asyncio
async def test_search_rejects_invalid_query_before_client(fake_session):
    with patch("zd_agent.tools.zendesk.call_zendesk_tool", side_effect=_mcp_must_not_call):
        result = await ZendeskSearch().execute({"query": ["status:new"]}, fake_session)

    assert result.success is False
    assert result.error == "query must be a string"


@pytest.mark.asyncio
async def test_search_articles_rejects_invalid_string_args_before_client(fake_session):
    tool = ZendeskSearchArticles()

    with patch("zd_agent.tools.zendesk.call_zendesk_tool", side_effect=_mcp_must_not_call):
        result = await tool.execute({"query": {"text": "workflow"}}, fake_session)

    assert result.success is False
    assert result.error == "query must be a string"

    with patch("zd_agent.tools.zendesk.call_zendesk_tool", side_effect=_mcp_must_not_call):
        result = await tool.execute({"query": "workflow", "locale": ["en-us"]}, fake_session)

    assert result.success is False
    assert result.error == "locale must be a string"


@pytest.mark.asyncio
async def test_get_article_rejects_invalid_args_before_client(fake_session):
    tool = ZendeskGetArticle()

    with patch("zd_agent.tools.zendesk.call_zendesk_tool", side_effect=_mcp_must_not_call):
        result = await tool.execute({"article_id": "123", "locale": "en-us"}, fake_session)

    assert result.success is False
    assert result.error == "article_id must be a positive integer"

    with patch("zd_agent.tools.zendesk.call_zendesk_tool", side_effect=_mcp_must_not_call):
        result = await tool.execute({"article_id": 123, "locale": {"lang": "en-us"}}, fake_session)

    assert result.success is False
    assert result.error == "locale must be a string"


@pytest.mark.asyncio
async def test_get_user_rejects_invalid_user_id_before_client(fake_session):
    with patch("zd_agent.tools.zendesk.call_zendesk_tool", side_effect=_mcp_must_not_call):
        result = await ZendeskGetUser().execute({"user_id": 0}, fake_session)

    assert result.success is False
    assert result.error == "user_id must be a positive integer"
