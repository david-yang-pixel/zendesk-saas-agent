"""Fail-closed tool registry for the v2 investigation runtime."""

from __future__ import annotations

from zd_agent.tools.base import Tool, ToolRegistry
from zd_agent.tools.document_indexing_status import DocumentIndexingStatus
from zd_agent.tools.git_source import (
    GitDiff,
    GitGrep,
    GitLog,
    GitShow,
    ListRepoTags,
    ListRepos,
)
from zd_agent.tools.github_pr import (
    GithubCodeSearch,
    GithubGetIssue,
    GithubGetPr,
    GithubSearchIssues,
    GithubSearchPrs,
)
from zd_agent.tools.inspector_exec import InspectorExec
from zd_agent.tools.official_docs import OfficialDocsRead, OfficialDocsSearch
from zd_agent.tools.saas_version import SaasDeployedVersion
from zd_agent.tools.scoped_queries import TenantScopedSignozQuery, TenantScopedSqlQuery
from zd_agent.tools.signoz_tools import (
    SignozErrors,
    SignozTraceback,
    structured_signoz_tools,
)
from zd_agent.tools.ticket_record import TicketRecordRead
from zd_agent.tools.zendesk import (
    ZendeskGetArticle,
    ZendeskGetTicket,
    ZendeskSearchArticles,
)


def _tenant_scoped_signoz_tools() -> tuple[Tool, ...]:
    return tuple(
        tool
        for tool in structured_signoz_tools()
        if "tenant" in tool.parameters.get("properties", {})
    )


def _casework_tools() -> tuple[Tool, ...]:
    return (
        ZendeskGetTicket(),
        ZendeskSearchArticles(),
        ZendeskGetArticle(),
        OfficialDocsSearch(),
        OfficialDocsRead(),
        GitShow(),
        GitGrep(),
        GitLog(),
        GitDiff(),
        ListRepos(),
        ListRepoTags(),
        GithubSearchPrs(),
        GithubGetPr(),
        GithubSearchIssues(),
        GithubGetIssue(),
        GithubCodeSearch(),
        SaasDeployedVersion(),
        DocumentIndexingStatus(),
        TenantScopedSqlQuery(),
        TenantScopedSignozQuery(),
        InspectorExec(),
        TicketRecordRead(),
        SignozErrors(),
        SignozTraceback(),
        *_tenant_scoped_signoz_tools(),
    )


def create_casework_tool_registry() -> ToolRegistry:
    """Build the complete v2 read surface without constructing mutation tools."""
    registry = ToolRegistry()
    for tool in _casework_tools():
        if tool.is_write_operation or tool.requires_sandbox:
            raise RuntimeError(f"unsafe tool registered for casework: {tool.name}")
        registry.register(tool)
    return registry
