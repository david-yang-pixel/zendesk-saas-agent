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
        "Current-ticket reads and Help Center reads execute through Zendesk MCP; "
        "cross-ticket search and arbitrary user lookup are not exposed to casework."
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
    "zendesk_search_articles": _ZENDESK_MCP,
    "zendesk_get_article": _ZENDESK_MCP,
    # Official documentation and local ticket audit records
    "official_docs_search": _OFFICIAL_DOCS_MCP,
    "official_docs_read": _OFFICIAL_DOCS_MCP,
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
    "github_search_issues": _boundary(
        "remote_mcp",
        "github-mcp",
        service_backed=True,
        fully_isolated=True,
        notes="GitHub Issue search executes through github-mcp.",
    ),
    "github_get_issue": _boundary(
        "remote_mcp",
        "github-mcp",
        service_backed=True,
        fully_isolated=True,
        notes="GitHub Issue reads execute through github-mcp.",
    ),
    "github_code_search": _boundary(
        "remote_mcp",
        "github-mcp",
        service_backed=True,
        fully_isolated=True,
        notes="GitHub code search executes through github-mcp.",
    ),
    # Database and production telemetry
    "inspector_exec": _boundary(
        "local_process",
        "inspector-cli",
        migration_target="tool-service/db-gateway",
        notes="Inspector still spawns a local process inside the agent container.",
    ),
    "document_indexing_status": _boundary(
        "remote_mcp",
        "db-gateway-mcp",
        service_backed=True,
        fully_isolated=True,
        notes="Fixed tenant-scoped document lifecycle query through DB Gateway MCP.",
    ),
    "sql_query": _boundary(
        "remote_mcp",
        "db-gateway-mcp",
        service_backed=True,
        fully_isolated=True,
        notes="Arbitrary read-only SQL protected by SQL write-safety validation.",
    ),
    "signoz_query": _boundary(
        "remote_mcp",
        "signoz-mcp",
        service_backed=True,
        fully_isolated=True,
        notes="Arbitrary read-only telemetry SQL protected by query safety validation.",
    ),
    "signoz_errors": _SIGNOZ_MCP,
    "signoz_traceback": _SIGNOZ_MCP,
    "signoz_services": _SIGNOZ_MCP,
    "signoz_list_services": _SIGNOZ_MCP,
    "signoz_get_trace_details": _SIGNOZ_MCP,
    "signoz_list_metrics": _SIGNOZ_MCP,
    "signoz_get_field_keys": _SIGNOZ_MCP,
    "signoz_get_field_values": _SIGNOZ_MCP,
    "signoz_search_logs": _SIGNOZ_MCP,
    "signoz_aggregate_logs": _SIGNOZ_MCP,
    "signoz_search_traces": _SIGNOZ_MCP,
    "signoz_aggregate_traces": _SIGNOZ_MCP,
    "signoz_query_metrics": _SIGNOZ_MCP,
}


def get_tool_service_boundary(tool_name: str) -> ToolServiceBoundary:
    try:
        return TOOL_SERVICE_BOUNDARIES[tool_name]
    except KeyError as exc:
        raise KeyError(
            f"tool service boundary is not declared for {tool_name!r}"
        ) from exc


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
