"""Tool Service boundary manifest tests."""

from zd_agent.casework.adapters.tool_registry import create_casework_tool_registry
from zd_agent.tools.service_boundary import (
    TOOL_SERVICE_BOUNDARIES,
    get_tool_service_boundary,
    summarize_tool_service_boundaries,
)


def test_every_registered_tool_declares_service_boundary() -> None:
    registry = create_casework_tool_registry()
    registered = {tool.name for tool in registry.list_tools()}

    assert registered == set(TOOL_SERVICE_BOUNDARIES)


def test_existing_split_services_are_marked_service_backed() -> None:
    signoz_search = get_tool_service_boundary("signoz_search_logs")
    assert signoz_search.execution_boundary == "remote_mcp"
    assert signoz_search.service == "signoz-mcp"
    assert signoz_search.service_backed is True
    assert signoz_search.fully_isolated is True

    zendesk_ticket = get_tool_service_boundary("zendesk_get_ticket")
    assert zendesk_ticket.execution_boundary == "remote_mcp"
    assert zendesk_ticket.service == "zendesk-mcp"
    assert zendesk_ticket.service_backed is True
    assert zendesk_ticket.fully_isolated is True


def test_removed_global_data_capabilities_have_no_casework_boundary() -> None:
    for name in ("zendesk_search", "zendesk_get_user"):
        assert name not in TOOL_SERVICE_BOUNDARIES


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

    github_issue = get_tool_service_boundary("github_search_issues")
    assert github_issue.execution_boundary == "remote_mcp"
    assert github_issue.service == "github-mcp"
    assert github_issue.service_backed is True
    assert github_issue.fully_isolated is True

    signoz_structured = get_tool_service_boundary("signoz_aggregate_traces")
    assert signoz_structured.execution_boundary == "remote_mcp"
    assert signoz_structured.service == "signoz-mcp"
    assert signoz_structured.service_backed is True
    assert signoz_structured.fully_isolated is True

    official_docs = get_tool_service_boundary("official_docs_search")
    assert official_docs.execution_boundary == "remote_mcp"
    assert official_docs.service == "docs-tool-service"
    assert official_docs.service_backed is True
    assert official_docs.fully_isolated is True


def test_boundary_summary_exposes_rollup_counts() -> None:
    registry = create_casework_tool_registry()
    summary = summarize_tool_service_boundaries(registry.list_tools())

    assert summary["total_tools"] == len(registry)
    assert summary["fully_isolated_tools"] >= 6
    assert summary["partial_tools"] >= 7
    assert summary["not_isolated_tools"] > 0
