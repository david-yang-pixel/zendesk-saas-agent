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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("args", "expected"),
    [
        ({"query": "workflow stuck", "version": ["1.14.2"]}, "version must be a string"),
        ({"query": "workflow stuck", "keyword": {"tag": "workflow"}}, "keyword must be a string"),
        ({"query": "workflow stuck", "priority": ["high"]}, "priority must be a string"),
        ({"query": "workflow stuck", "priority": "critical"}, "invalid priority 'critical'"),
    ],
)
async def test_kg_search_rejects_invalid_optional_filters_before_call(
    fake_session,
    tmp_path,
    monkeypatch,
    args,
    expected,
):
    kg_bin = tmp_path / "zendesk-kg"
    kg_bin.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr(zendesk_kg, "_KG_BIN", kg_bin)

    async def fake_run_kg(*_args, **_kwargs):
        raise AssertionError("_run_kg should not be called")
    monkeypatch.setattr(zendesk_kg, "_run_kg", fake_run_kg)

    result = await ZendeskKgSearch().execute(args, fake_session)

    assert result.success is False
    assert expected in (result.error or "")
