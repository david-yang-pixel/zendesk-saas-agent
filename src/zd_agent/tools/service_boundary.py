"""Declared execution boundaries for registered agent tools.

This module is intentionally explicit: every registered tool must have an
entry here, so new tools cannot silently bypass the Tool Service migration
plan.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable, Literal


ExecutionBoundary = Literal[
    "remote_mcp",
    "remote_http_relay",
    "mixed_remote_local",
    "direct_external_api",
    "local_process",
    "local_files",
    "in_process",
]
MigrationStatus = Literal["isolated", "partial", "not_isolated"]


@dataclass(frozen=True)
class ToolServiceBoundary:
    execution_boundary: ExecutionBoundary
    service: str
    service_backed: bool
    fully_isolated: bool
    migration_status: MigrationStatus
    migration_target: str | None = None
    notes: str = ""

    def to_metadata(self) -> dict[str, Any]:
        return asdict(self)


def _boundary(
    execution_boundary: ExecutionBoundary,
    service: str,
    *,
    service_backed: bool = False,
    fully_isolated: bool = False,
    migration_target: str | None = None,
    notes: str = "",
) -> ToolServiceBoundary:
    if fully_isolated:
        migration_status: MigrationStatus = "isolated"
    elif service_backed:
        migration_status = "partial"
    else:
        migration_status = "not_isolated"
    return ToolServiceBoundary(
        execution_boundary=execution_boundary,
        service=service,
        service_backed=service_backed,
        fully_isolated=fully_isolated,
        migration_status=migration_status,
        migration_target=migration_target,
        notes=notes,
    )


_ZENDESK_MCP = _boundary(
    "remote_mcp",
    "zendesk-mcp",
    service_backed=True,
    fully_isolated=True,
    notes=(
        "Ticket reads, searches, Help Center reads, attachment discovery/download, "
        "user lookup, internal notes, metadata writes, and follow operations "
        "execute through Zendesk MCP."
    ),
)
_LOCAL_KNOWLEDGE = _boundary(
    "local_files",
    "agent-local-files",
    migration_target="tool-service/knowledge",
)
_OFFICIAL_DOCS_MCP = _boundary(
    "remote_mcp",
    "docs-tool-service",
    service_backed=True,
    fully_isolated=True,
    notes="Official docs search/read executes through docs-tool-service MCP.",
)
_SOURCE_PARTIAL = _boundary(
    "mixed_remote_local",
    "source-mcp",
    service_backed=True,
    migration_target="tool-service/source-tool-service",
    notes="Uses Source MCP when configured, with local git fallback still present.",
)
_SIGNOZ_MCP = _boundary(
    "remote_mcp",
    "signoz-mcp",
    service_backed=True,
    fully_isolated=True,
)


TOOL_SERVICE_BOUNDARIES: dict[str, ToolServiceBoundary] = {
    # Zendesk tools
    "zendesk_get_ticket": _ZENDESK_MCP,
    "zendesk_get_attachments": _ZENDESK_MCP,
    "zendesk_search": _ZENDESK_MCP,
    "zendesk_search_articles": _ZENDESK_MCP,
    "zendesk_get_article": _ZENDESK_MCP,
    "zendesk_get_user": _ZENDESK_MCP,
    "zendesk_internal_reply": _ZENDESK_MCP,
    "zendesk_add_tags": _ZENDESK_MCP,
    "zendesk_follow_ticket": _ZENDESK_MCP,
    "zendesk_public_reply": _boundary(
        "remote_mcp",
        "zendesk-browser-relay",
        service_backed=True,
        fully_isolated=True,
        notes="Customer-visible browser reply executes through the zendesk-browser-relay MCP endpoint.",
    ),
    # Knowledge and local records
    "knowledge_search": _LOCAL_KNOWLEDGE,
    "knowledge_read": _LOCAL_KNOWLEDGE,
    "official_docs_search": _OFFICIAL_DOCS_MCP,
    "official_docs_read": _OFFICIAL_DOCS_MCP,
    "ticket_record_search": _LOCAL_KNOWLEDGE,
    "ticket_record_read": _LOCAL_KNOWLEDGE,
    # Source tools
    "git_show": _SOURCE_PARTIAL,
    "git_grep": _SOURCE_PARTIAL,
    "git_log": _SOURCE_PARTIAL,
    "git_diff": _SOURCE_PARTIAL,
    "list_repos": _SOURCE_PARTIAL,
    "list_repo_tags": _SOURCE_PARTIAL,
    "saas_deployed_version": _boundary(
        "mixed_remote_local",
        "source-mcp/signoz-mcp",
        service_backed=True,
        migration_target="tool-service/source-tool-service",
        notes="Uses Source MCP when configured, then falls back to local deployment data.",
    ),
    "github_search_prs": _boundary(
        "remote_mcp",
        "github-mcp",
        service_backed=True,
        fully_isolated=True,
        notes="GitHub PR search executes through github-mcp.",
    ),
    "github_get_pr": _boundary(
        "remote_mcp",
        "github-mcp",
        service_backed=True,
        fully_isolated=True,
        notes="GitHub PR reads execute through github-mcp.",
    ),
    "github_code_search": _boundary(
        "remote_mcp",
        "github-mcp",
        service_backed=True,
        fully_isolated=True,
        notes="GitHub code search executes through github-mcp.",
    ),
    "saas_branch_switch": _boundary(
        "local_process",
        "agent-container",
        migration_target="tool-service/source-tool-service",
    ),
    # Database and production telemetry
    "sql_query": _boundary(
        "remote_mcp",
        "db-gateway",
        service_backed=True,
        fully_isolated=True,
        notes="Read-only SQL executes through the DB Gateway MCP service.",
    ),
    "inspector_exec": _boundary(
        "local_process",
        "inspector-cli",
        migration_target="tool-service/db-gateway",
        notes="Inspector still spawns a local process inside the agent container.",
    ),
    "signoz_errors": _SIGNOZ_MCP,
    "signoz_traceback": _SIGNOZ_MCP,
    "signoz_services": _SIGNOZ_MCP,
    "signoz_query": _SIGNOZ_MCP,
    # Local environment and sandbox tools
    "run_script": _boundary(
        "remote_mcp",
        "sandbox-worker",
        service_backed=True,
        fully_isolated=True,
        notes="Diagnostic scripts execute through sandbox-worker MCP.",
    ),
    "sandbox_exec": _boundary(
        "remote_mcp",
        "sandbox-worker",
        service_backed=True,
        fully_isolated=True,
        notes="Sandbox commands execute through sandbox-worker MCP.",
    ),
    "sandbox_list": _boundary(
        "remote_mcp",
        "sandbox-worker",
        service_backed=True,
        fully_isolated=True,
        notes="Sandbox listing executes through sandbox-worker MCP.",
    ),
    "sandbox_search": _boundary(
        "remote_mcp",
        "sandbox-worker",
        service_backed=True,
        fully_isolated=True,
        notes="Sandbox search executes through sandbox-worker MCP.",
    ),
    "sandbox_write": _boundary(
        "remote_mcp",
        "sandbox-worker",
        service_backed=True,
        fully_isolated=True,
        notes="Sandbox writes execute through sandbox-worker MCP.",
    ),
    "sandbox_read": _boundary(
        "remote_mcp",
        "sandbox-worker",
        service_backed=True,
        fully_isolated=True,
        notes="Sandbox reads execute through sandbox-worker MCP.",
    ),
    # Workflow/orchestration tools
    "classify_ticket": _boundary(
        "in_process",
        "agent-process",
        migration_target="tool-service/workflow",
    ),
    "archive_ticket": _boundary(
        "in_process",
        "agent-process",
        migration_target="tool-service/workflow",
    ),
    "run_subagent": _boundary(
        "in_process",
        "agent-process",
        migration_target="tool-service/orchestrator",
    ),
    "run_subagents_parallel": _boundary(
        "in_process",
        "agent-process",
        migration_target="tool-service/orchestrator",
    ),
    # Notifications
    "feishu_push": _boundary(
        "remote_mcp",
        "notification-service",
        service_backed=True,
        fully_isolated=True,
        notes="Feishu credentials and retry logic live in notification-service MCP.",
    ),
    "custom_mcp_call": _boundary(
        "remote_mcp",
        "admin-configured-mcp",
        service_backed=True,
        fully_isolated=True,
        notes="Calls an enabled MCP service configured from the admin console.",
    ),
}


def get_tool_service_boundary(tool_name: str) -> ToolServiceBoundary:
    try:
        return TOOL_SERVICE_BOUNDARIES[tool_name]
    except KeyError as exc:
        raise KeyError(f"tool service boundary is not declared for {tool_name!r}") from exc


def summarize_tool_service_boundaries(tools: Iterable[Any]) -> dict[str, Any]:
    boundaries = [get_tool_service_boundary(tool.name) for tool in tools]
    by_status: dict[str, int] = {"isolated": 0, "partial": 0, "not_isolated": 0}
    by_boundary: dict[str, int] = {}
    for boundary in boundaries:
        by_status[boundary.migration_status] += 1
        by_boundary[boundary.execution_boundary] = (
            by_boundary.get(boundary.execution_boundary, 0) + 1
        )

    return {
        "total_tools": len(boundaries),
        "fully_isolated_tools": by_status["isolated"],
        "partial_tools": by_status["partial"],
        "not_isolated_tools": by_status["not_isolated"],
        "by_status": by_status,
        "by_execution_boundary": dict(sorted(by_boundary.items())),
    }
