"""工具注册入口：汇总所有工具并统一注册"""

from __future__ import annotations

from zd_agent.tools.archive import ArchiveTicket
from zd_agent.tools.base import Tool, ToolRegistry
from zd_agent.tools.classify import ClassifyTicket
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
from zd_agent.tools.feishu_tools import FeishuPush
from zd_agent.tools.saas_branch import SaasBranchSwitch
from zd_agent.tools.inspector_exec import InspectorExec
from zd_agent.tools.sql_query import SqlQuery
from zd_agent.tools.ticket_record import TicketRecordRead, TicketRecordSearch
from zd_agent.tools.subagent import RunSubagent
from zd_agent.tools.subagent_parallel import RunSubagentsParallel
from zd_agent.tools.zendesk import (
    ZendeskAddTags,
    ZendeskFollowTicket,
    ZendeskGetArticle,
    ZendeskGetAttachments,
    ZendeskGetTicket,
    ZendeskGetUser,
    ZendeskInternalReply,
    ZendeskSearch,
    ZendeskSearchArticles,
)
from zd_agent.tools.zendesk_public_reply import ZendeskPublicReply
from zd_agent.tools.saas_version import SaasDeployedVersion
from zd_agent.tools.signoz_tools import (
    SignozErrors,
    SignozTraceback,
    SignozServices,
    SignozQuery,
)


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
    registry.register(ZendeskPublicReply())  # ⚠️ 受 cfg.public_reply.enabled 门控
    registry.register(ZendeskAddTags())
    registry.register(ZendeskFollowTicket())

    # 知识库工具组
    registry.register(KnowledgeSearch())
    registry.register(KnowledgeRead())
    registry.register(OfficialDocsSearch())
    registry.register(OfficialDocsRead())

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

    # 线上版本感知（SigNoz 实时遥测 + saas-deploy 兜底）
    registry.register(SaasDeployedVersion())

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
    # InspectorExec 优先于 SqlQuery：quota / workflow run / UUID 类型未知
    # 类查询用 inspector，跨 main+billing+TiDB 自动聚合，避免手写 SQL 漏字段
    registry.register(InspectorExec())
    registry.register(SqlQuery())

    # 历史工单记录检索工具组（SaaS 知识复用）
    registry.register(TicketRecordSearch())
    registry.register(TicketRecordRead())

    # 飞书通知工具（主动推送）
    registry.register(FeishuPush())

    # SigNoz 日志查询工具组（经远程 sz MCP）
    registry.register(SignozErrors())
    registry.register(SignozTraceback())
    registry.register(SignozServices())
    registry.register(SignozQuery())

    return registry
