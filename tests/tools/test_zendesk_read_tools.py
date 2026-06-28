from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from zd_agent.tools.zendesk import (
    ZendeskGetArticle,
    ZendeskGetAttachments,
    ZendeskGetTicket,
    ZendeskGetUser,
    ZendeskSearch,
    ZendeskSearchArticles,
)


def _client_must_not_initialize():
    raise AssertionError("client should not be initialized for invalid args")


@pytest.mark.asyncio
async def test_get_ticket_rejects_invalid_ticket_id_before_client(fake_session):
    with patch("zd_agent.tools.zendesk._get_client", side_effect=_client_must_not_initialize):
        result = await ZendeskGetTicket().execute({"ticket_id": "12345"}, fake_session)

    assert result.success is False
    assert result.error == "ticket_id must be a positive integer"


@pytest.mark.asyncio
async def test_get_attachments_rejects_invalid_ticket_id_before_client(fake_session):
    with patch("zd_agent.tools.zendesk._get_client", side_effect=_client_must_not_initialize):
        result = await ZendeskGetAttachments().execute({"ticket_id": True}, fake_session)

    assert result.success is False
    assert result.error == "ticket_id must be a positive integer"


@pytest.mark.asyncio
async def test_get_attachments_detects_native_html_inline_image(tmp_path, fake_session):
    fake_session.sandbox_dir = tmp_path
    client = MagicMock()
    client.get_ticket.return_value = {
        "ticket": {"id": 12345, "via": {"channel": "native_messaging"}},
    }
    events = [
        {
            "id": "evt-1",
            "created_at": "2026-06-27T10:11:18Z",
            "author": {"type": "end-user"},
            "content": {
                "type": "html",
                "body": '<p>Screenshot</p><img src="https://img.example.com/a.png">',
            },
        },
    ]
    downloaded: dict[str, Path] = {
        "https://img.example.com/a.png": tmp_path / "attachments" / "a.png",
    }

    async def _passthrough(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    with patch("zd_agent.tools.zendesk._get_client", return_value=client), \
         patch("zd_agent.tools.zendesk._call_with_rate_limit_retry", side_effect=_passthrough), \
         patch("zd_agent.tools.zendesk._get_ticket_thread", return_value=("conversation_log", events)), \
         patch("zd_agent.tools.zendesk._download_ticket_images", return_value=(downloaded, [], {})) as dl:
        result = await ZendeskGetAttachments().execute({"ticket_id": 12345}, fake_session)

    assert result.success is True
    assert "该工单没有附件或图片" not in result.output
    assert "已下载 1 个文件" in result.output
    assert dl.call_args.args[1][0]["url"] == "https://img.example.com/a.png"


@pytest.mark.asyncio
async def test_search_rejects_invalid_query_before_client(fake_session):
    with patch("zd_agent.tools.zendesk._get_client", side_effect=_client_must_not_initialize):
        result = await ZendeskSearch().execute({"query": ["status:new"]}, fake_session)

    assert result.success is False
    assert result.error == "query must be a string"


@pytest.mark.asyncio
async def test_search_articles_rejects_invalid_string_args_before_client(fake_session):
    tool = ZendeskSearchArticles()

    with patch("zd_agent.tools.zendesk._get_client", side_effect=_client_must_not_initialize):
        result = await tool.execute({"query": {"text": "workflow"}}, fake_session)

    assert result.success is False
    assert result.error == "query must be a string"

    with patch("zd_agent.tools.zendesk._get_client", side_effect=_client_must_not_initialize):
        result = await tool.execute({"query": "workflow", "locale": ["en-us"]}, fake_session)

    assert result.success is False
    assert result.error == "locale must be a string"


@pytest.mark.asyncio
async def test_get_article_rejects_invalid_args_before_client(fake_session):
    tool = ZendeskGetArticle()

    with patch("zd_agent.tools.zendesk._get_client", side_effect=_client_must_not_initialize):
        result = await tool.execute({"article_id": "123", "locale": "en-us"}, fake_session)

    assert result.success is False
    assert result.error == "article_id must be a positive integer"

    with patch("zd_agent.tools.zendesk._get_client", side_effect=_client_must_not_initialize):
        result = await tool.execute({"article_id": 123, "locale": {"lang": "en-us"}}, fake_session)

    assert result.success is False
    assert result.error == "locale must be a string"


@pytest.mark.asyncio
async def test_get_user_rejects_invalid_user_id_before_client(fake_session):
    with patch("zd_agent.tools.zendesk._get_client", side_effect=_client_must_not_initialize):
        result = await ZendeskGetUser().execute({"user_id": 0}, fake_session)

    assert result.success is False
    assert result.error == "user_id must be a positive integer"
