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
from zd_agent.tools.signoz_tools import (
    SignozErrors,
    SignozQuery,
    SignozServices,
    SignozTraceback,
    structured_signoz_tools,
)
from zd_agent.tools.sql_query import SqlQuery
from zd_agent.tools.ticket_record import TicketRecordRead
from zd_agent.tools.zendesk import (
    ZendeskGetArticle,
    ZendeskGetTicket,
    ZendeskSearchArticles,
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
        SqlQuery(),
        SignozQuery(),
        InspectorExec(),
        TicketRecordRead(),
        SignozErrors(),
        SignozTraceback(),
        SignozServices(),
        *structured_signoz_tools(),
    )


def create_casework_tool_registry() -> ToolRegistry:
    """Build the canonical ZendeskCasework business-tool surface."""
    registry = ToolRegistry()
    for tool in _casework_tools():
        if tool.is_write_operation or tool.requires_sandbox:
            raise RuntimeError(f"unsafe tool registered for casework: {tool.name}")
        registry.register(tool)
    return registry
