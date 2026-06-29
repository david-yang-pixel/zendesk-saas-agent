"""Tool Service boundary manifest tests."""

from zd_agent.tools.registry import create_tool_registry
from zd_agent.tools.service_boundary import (
    TOOL_SERVICE_BOUNDARIES,
    get_tool_service_boundary,
    summarize_tool_service_boundaries,
)


def test_every_registered_tool_declares_service_boundary() -> None:
    registry = create_tool_registry()
    registered = {tool.name for tool in registry.list_tools()}

    assert registered == set(TOOL_SERVICE_BOUNDARIES)


def test_existing_split_services_are_marked_service_backed() -> None:
    sql_query = get_tool_service_boundary("sql_query")
    assert sql_query.execution_boundary == "remote_mcp"
    assert sql_query.service == "db-gateway"
    assert sql_query.service_backed is True
    assert sql_query.fully_isolated is True

    signoz_query = get_tool_service_boundary("signoz_query")
    assert signoz_query.execution_boundary == "remote_mcp"
    assert signoz_query.service == "signoz-mcp"
    assert signoz_query.service_backed is True
    assert signoz_query.fully_isolated is True

    public_reply = get_tool_service_boundary("zendesk_public_reply")
    assert public_reply.execution_boundary == "remote_mcp"
    assert public_reply.service == "zendesk-browser-relay"
    assert public_reply.service_backed is True
    assert public_reply.fully_isolated is True

    zendesk_ticket = get_tool_service_boundary("zendesk_get_ticket")
    assert zendesk_ticket.execution_boundary == "remote_mcp"
    assert zendesk_ticket.service == "zendesk-mcp"
    assert zendesk_ticket.service_backed is True
    assert zendesk_ticket.fully_isolated is True

    internal_reply = get_tool_service_boundary("zendesk_internal_reply")
    assert internal_reply.execution_boundary == "remote_mcp"
    assert internal_reply.service == "zendesk-mcp"
    assert internal_reply.fully_isolated is True

    follow_ticket = get_tool_service_boundary("zendesk_follow_ticket")
    assert follow_ticket.execution_boundary == "remote_mcp"
    assert follow_ticket.service == "zendesk-mcp"
    assert follow_ticket.fully_isolated is True

    attachments = get_tool_service_boundary("zendesk_get_attachments")
    assert attachments.execution_boundary == "remote_mcp"
    assert attachments.service == "zendesk-mcp"
    assert attachments.service_backed is True
    assert attachments.fully_isolated is True


def test_source_tools_are_partial_until_local_fallback_is_removed() -> None:
    for name in [
        "git_show",
        "git_grep",
        "git_log",
        "git_diff",
        "list_repos",
        "list_repo_tags",
        "saas_deployed_version",
    ]:
        boundary = get_tool_service_boundary(name)
        assert boundary.service_backed is True
        assert boundary.fully_isolated is False
        assert boundary.migration_status == "partial"


def test_remaining_unisolated_high_risk_tools_are_migration_candidates() -> None:
    inspector = get_tool_service_boundary("inspector_exec")
    assert inspector.execution_boundary == "local_process"
    assert inspector.service_backed is False
    assert inspector.fully_isolated is False
    assert inspector.migration_target == "tool-service/db-gateway"


def test_next_batch_tools_are_fully_isolated_mcp_services() -> None:
    github_pr = get_tool_service_boundary("github_search_prs")
    assert github_pr.execution_boundary == "remote_mcp"
    assert github_pr.service == "github-mcp"
    assert github_pr.service_backed is True
    assert github_pr.fully_isolated is True

    official_docs = get_tool_service_boundary("official_docs_search")
    assert official_docs.execution_boundary == "remote_mcp"
    assert official_docs.service == "docs-tool-service"
    assert official_docs.service_backed is True
    assert official_docs.fully_isolated is True

    feishu = get_tool_service_boundary("feishu_push")
    assert feishu.execution_boundary == "remote_mcp"
    assert feishu.service == "notification-service"
    assert feishu.service_backed is True
    assert feishu.fully_isolated is True

    sandbox = get_tool_service_boundary("sandbox_exec")
    assert sandbox.execution_boundary == "remote_mcp"
    assert sandbox.service == "sandbox-worker"
    assert sandbox.service_backed is True
    assert sandbox.fully_isolated is True


def test_boundary_summary_exposes_rollup_counts() -> None:
    registry = create_tool_registry()
    summary = summarize_tool_service_boundaries(registry.list_tools())

    assert summary["total_tools"] == len(registry)
    assert summary["fully_isolated_tools"] >= 6
    assert summary["partial_tools"] >= 7
    assert summary["not_isolated_tools"] > 0
