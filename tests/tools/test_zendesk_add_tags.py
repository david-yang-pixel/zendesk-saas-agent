import pytest

from zd_agent.tools.zendesk import ZendeskAddTags


@pytest.mark.asyncio
async def test_add_tags_rejects_invalid_arg_types(fake_session):
    fake_session.dry_run = True
    tool = ZendeskAddTags()

    result = await tool.execute(
        {"ticket_id": "12345", "tags": ["bug"]},
        fake_session,
    )
    assert result.success is False
    assert "ticket_id" in (result.error or "")

    result = await tool.execute(
        {"ticket_id": 12345, "tags": "bug"},
        fake_session,
    )
    assert result.success is False
    assert "tags" in (result.error or "")

    result = await tool.execute(
        {"ticket_id": 12345, "tags": ["bug", 123]},
        fake_session,
    )
    assert result.success is False
    assert "tags" in (result.error or "")


@pytest.mark.asyncio
async def test_add_tags_dry_run_accepts_allowed_tags(fake_session):
    fake_session.dry_run = True
    result = await ZendeskAddTags().execute(
        {"ticket_id": 12345, "tags": ["Bug", "helm", "bug"]},
        fake_session,
    )

    assert result.success is True
    assert "bug" in (result.output or "")
    assert "helm" in (result.output or "")
