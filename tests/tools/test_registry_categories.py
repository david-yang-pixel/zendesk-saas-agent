"""Test get_tools_for_category helper."""
from zd_agent.tools.registry import (
    create_tool_registry,
    get_tools_for_category,
)


def test_always_group_always_included():
    reg = create_tool_registry()
    # Any category should include run_subagents_parallel (group=always)
    for cat in ["platform_issue", "refund", "insufficient_info", "bogus_category"]:
        tools = get_tools_for_category(reg, cat)
        names = {t.name for t in tools}
        assert "run_subagents_parallel" in names
        assert "classify_ticket" in names  # workflow group always included
        assert "archive_ticket" in names


def test_platform_issue_includes_grafana_signoz():
    reg = create_tool_registry()
    tools = get_tools_for_category(reg, "platform_issue")
    names = {t.name for t in tools}
    # Grafana/SigNoz tools only registered if API keys configured — may or may not appear.
    # But SQL and ticket_record MUST be present via database_query + knowledge groups:
    assert "sql_query" in names
    assert "ticket_record_search" in names


def test_insufficient_info_is_minimal():
    reg = create_tool_registry()
    tools = get_tools_for_category(reg, "insufficient_info")
    names = {t.name for t in tools}
    # Should include 'always' + 'workflow' + 'subagent' only
    assert "sql_query" not in names
    assert "ticket_record_search" not in names
    # But still has archive + classify + feishu_push + run_subagent(s)
    assert "archive_ticket" in names
    assert "classify_ticket" in names
    assert "feishu_push" in names


def test_refund_includes_sql_and_knowledge():
    reg = create_tool_registry()
    tools = get_tools_for_category(reg, "refund")
    names = {t.name for t in tools}
    assert "sql_query" in names
    assert "ticket_record_search" in names


def test_unknown_category_falls_back_to_general():
    reg = create_tool_registry()
    tools_general = {t.name for t in get_tools_for_category(reg, "general")}
    tools_unknown = {t.name for t in get_tools_for_category(reg, "not-a-real-cat")}
    assert tools_general == tools_unknown


def test_api_category_has_knowledge_but_not_sql():
    reg = create_tool_registry()
    tools = get_tools_for_category(reg, "api")
    names = {t.name for t in tools}
    assert "ticket_record_search" in names
    # per mapping, 'api' does not include database_query
    assert "sql_query" not in names
