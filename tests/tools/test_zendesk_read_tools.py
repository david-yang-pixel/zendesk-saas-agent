from unittest.mock import patch

import pytest

from zd_agent.tools.zendesk import (
    ZendeskGetArticle,
    ZendeskGetAttachments,
    ZendeskGetTicket,
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
