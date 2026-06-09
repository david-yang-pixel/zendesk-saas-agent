import pytest

from zd_agent.tools import zendesk_kg
from zd_agent.tools.zendesk_kg import ZendeskKgSearch


@pytest.mark.asyncio
async def test_kg_search_rejects_invalid_top_k(fake_session, tmp_path, monkeypatch):
    kg_bin = tmp_path / "zendesk-kg"
    kg_bin.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr(zendesk_kg, "_KG_BIN", kg_bin)

    tool = ZendeskKgSearch()
    result = await tool.execute(
        {"query": "workflow stuck", "top_k": "many"},
        fake_session,
    )

    assert result.success is False
    assert "top_k" in (result.error or "")


@pytest.mark.asyncio
async def test_kg_search_rejects_non_string_query(fake_session, tmp_path, monkeypatch):
    kg_bin = tmp_path / "zendesk-kg"
    kg_bin.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr(zendesk_kg, "_KG_BIN", kg_bin)

    tool = ZendeskKgSearch()
    result = await tool.execute(
        {"query": {"text": "workflow stuck"}},
        fake_session,
    )

    assert result.success is False
    assert result.error == "query must be a string"
