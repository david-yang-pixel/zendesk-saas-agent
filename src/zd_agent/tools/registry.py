"""工具注册入口：汇总所有工具并统一注册"""

from __future__ import annotations

from zd_agent.tools.archive import ArchiveTicket
from zd_agent.tools.base import Tool, ToolRegistry
from zd_agent.tools.classify import ClassifyTicket
from zd_agent.tools.grafana_tools import (
    GrafanaGetDashboard,
    GrafanaListDashboards,
    GrafanaQueryPrometheus,
)
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
    GithubGetPr,
    GithubSearchPrs,
)
from zd_agent.tools.knowledge import KnowledgeRead, KnowledgeSearch
from zd_agent.tools.official_docs import OfficialDocsRead, OfficialDocsSearch
from zd_agent.tools.sandbox_tools import (
    SandboxList,
    SandboxRead,
    SandboxSearch,
    SandboxWrite,
)
from zd_agent.tools.script_runner import RunScript, SandboxExec
from zd_agent.tools.signoz_tools import (
    SignozGetFieldKeys,
    SignozGetFieldValues,
    SignozListServices,
    SignozSearchLogs,
    SignozSearchTraces,
)
from zd_agent.tools.feishu_tools import FeishuPush
from zd_agent.tools.saas_branch import SaasBranchSwitch
from zd_agent.tools.sql_query import SqlQuery
from zd_agent.tools.ticket_record import TicketRecordRead, TicketRecordSearch
from zd_agent.tools.subagent import RunSubagent
from zd_agent.tools.subagent_parallel import RunSubagentsParallel
from zd_agent.tools.zendesk_kg import ZendeskKgSearch
from zd_agent.tools.zendesk import (
    ZendeskAddTags,
    ZendeskGetArticle,
    ZendeskGetAttachments,
    ZendeskGetTicket,
    ZendeskGetUser,
    ZendeskInternalReply,
    ZendeskSearch,
    ZendeskSearchArticles,
)
from zd_agent.tools.zd_kb_cli import ZdKbCliExec


def create_tool_registry() -> ToolRegistry:
    """创建并返回包含所有工具的注册中心"""
    registry = ToolRegistry()

    # Zendesk 工具组
    registry.register(ZendeskGetTicket())
    registry.register(ZendeskGetAttachments())
    registry.register(ZendeskSearch())
    registry.register(ZendeskSearchArticles())
    registry.register(ZendeskGetArticle())
    registry.register(ZendeskGetUser())
    registry.register(ZendeskInternalReply())
    registry.register(ZendeskAddTags())

    # 知识库工具组
    registry.register(KnowledgeSearch())
    registry.register(KnowledgeRead())
    registry.register(ZendeskKgSearch())
    registry.register(OfficialDocsSearch())
    registry.register(OfficialDocsRead())
    registry.register(ZdKbCliExec())

    # 源码工具组
    registry.register(GitShow())
    registry.register(GitGrep())
    registry.register(GitLog())
    registry.register(GitDiff())
    registry.register(ListRepos())
    registry.register(ListRepoTags())
    registry.register(GithubSearchPrs())
    registry.register(GithubGetPr())
    registry.register(GithubCodeSearch())

    # 环境工具组
    registry.register(RunScript())
    registry.register(SandboxExec())

    # Sandbox 工具组
    registry.register(SandboxList())
    registry.register(SandboxSearch())
    registry.register(SandboxWrite())
    registry.register(SandboxRead())

    # 分类工具
    registry.register(ClassifyTicket())

    # 归档工具组
    registry.register(ArchiveTicket())

    # Subagent 工具
    registry.register(RunSubagent())
    registry.register(RunSubagentsParallel())

    # SaaS 仓库分支切换工具
    registry.register(SaasBranchSwitch())

    # 数据库只读查询工具组（SaaS）
    registry.register(SqlQuery())

    # 历史工单记录检索工具组（SaaS 知识复用）
    registry.register(TicketRecordSearch())
    registry.register(TicketRecordRead())

    # 飞书通知工具（主动推送）
    registry.register(FeishuPush())

    # Grafana 监控工具组（条件注册：仅当配置了 URL + API key）
    from zd_agent.config import get_config
    _gcfg = get_config().grafana
    if _gcfg.url and _gcfg.api_key:
        registry.register(GrafanaListDashboards())
        registry.register(GrafanaGetDashboard())
        registry.register(GrafanaQueryPrometheus())

    # SigNoz 日志/追踪工具组（条件注册：仅当配置了 URL + API key）
    _scfg = get_config().signoz
    if _scfg.url and _scfg.api_key:
        registry.register(SignozSearchLogs())
        registry.register(SignozSearchTraces())
        registry.register(SignozListServices())
        registry.register(SignozGetFieldKeys())
        registry.register(SignozGetFieldValues())

    return registry
