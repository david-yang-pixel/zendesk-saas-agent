"""GitHub PR, Issue, and code-search read-only tools."""

from __future__ import annotations

from typing import Any

from zd_agent.integrations.github_mcp import call_github_tool
from zd_agent.session.agent_session import AgentSession
from zd_agent.tools.base import Tool, ToolResult


def _coerce_int_arg(
    args: dict[str, Any],
    name: str,
    default: int | None = None,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int | ToolResult:
    raw = args.get(name, default)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return ToolResult(success=False, error=f"{name} must be an integer")
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def _coerce_optional_string_arg(args: dict[str, Any], name: str) -> str | ToolResult:
    raw = args.get(name)
    if raw is None:
        return ""
    if not isinstance(raw, str):
        return ToolResult(success=False, error=f"{name} must be a string")
    return raw.strip()


def _coerce_required_string_arg(args: dict[str, Any], name: str) -> str | ToolResult:
    raw = args.get(name)
    if not isinstance(raw, str):
        return ToolResult(success=False, error=f"{name} must be a string")
    return raw.strip()


def _mcp_failure(
    payload: dict[str, Any], *, service_name: str = "GitHub MCP"
) -> ToolResult:
    code = payload.get("code") or "UNKNOWN"
    error = payload.get("error") or "unknown error"
    return ToolResult(success=False, error=f"{service_name} failed [{code}]: {error}")


def _payload_output(payload: dict[str, Any]) -> str:
    output = payload.get("output")
    if isinstance(output, str) and output.strip():
        return output
    text = payload.get("text")
    if isinstance(text, str) and text.strip():
        return text
    return ""


def _payload_data(payload: dict[str, Any]) -> dict[str, Any]:
    data = payload.get("data")
    return data if isinstance(data, dict) else {}


def _format_pr_search_output(
    *,
    repo: str,
    query: str,
    limit: int,
    data: dict[str, Any],
) -> ToolResult:
    items = data.get("items", [])
    if not items:
        return ToolResult(output=f"未找到与「{query}」相关的 GitHub PR（repo: {repo}）")

    lines = ["# GitHub PR 搜索结果", f"Repo: {repo}", f"Query: {query}", ""]
    for item in items[:limit]:
        number = item.get("number", "N/A")
        title = item.get("title", "(无标题)")
        state = item.get("state", "unknown")
        author = (item.get("user") or {}).get("login", "unknown")
        url = item.get("html_url", "")
        lines.append(f"- #{number} [{state}] {title}")
        lines.append(f"  作者: {author}")
        if url:
            lines.append(f"  链接: {url}")
    return ToolResult(output="\n".join(lines))


def _format_pr_detail_output(
    *,
    repo: str,
    pr_number: int,
    data: dict[str, Any],
) -> ToolResult:
    title = data.get("title", "(无标题)")
    state = data.get("state", "unknown")
    merged_at = data.get("merged_at")
    status = "merged" if merged_at else state
    author = (data.get("user") or {}).get("login", "unknown")
    body = (data.get("body") or "").strip()
    if len(body) > 1200:
        body = body[:1200] + "\n\n... (摘要已截断)"

    lines = [
        "# GitHub PR 详情",
        f"Repo: {repo}",
        f"PR: #{pr_number}",
        f"标题: {title}",
        f"状态: {status}",
        f"作者: {author}",
    ]
    if merged_at:
        lines.append(f"合并时间: {merged_at}")
    html_url = data.get("html_url")
    if html_url:
        lines.append(f"链接: {html_url}")
    if body:
        lines.extend(["", body])
    return ToolResult(output="\n".join(lines))


def _format_issue_search_output(
    *,
    repo: str,
    query: str,
    limit: int,
    data: dict[str, Any],
) -> ToolResult:
    items = data.get("items", [])
    if not items:
        return ToolResult(
            output=f"未找到与「{query}」相关的 GitHub Issue（repo: {repo}）"
        )

    lines = ["# GitHub Issue 搜索结果", f"Repo: {repo}", f"Query: {query}", ""]
    for item in items[:limit]:
        number = item.get("number", "N/A")
        title = item.get("title", "(无标题)")
        state = item.get("state", "unknown")
        author = (item.get("user") or {}).get("login", "unknown")
        labels = [
            label.get("name", "")
            for label in (item.get("labels") or [])
            if isinstance(label, dict) and label.get("name")
        ]
        url = item.get("html_url", "")
        lines.append(f"- #{number} [{state}] {title}")
        lines.append(f"  作者: {author}")
        if labels:
            lines.append(f"  Labels: {', '.join(labels)}")
        if url:
            lines.append(f"  链接: {url}")
    return ToolResult(output="\n".join(lines))


def _format_issue_detail_output(
    *,
    repo: str,
    issue_number: int,
    data: dict[str, Any],
) -> ToolResult:
    title = data.get("title", "(无标题)")
    state = data.get("state", "unknown")
    state_reason = data.get("state_reason")
    author = (data.get("user") or {}).get("login", "unknown")
    labels = [
        label.get("name", "")
        for label in (data.get("labels") or [])
        if isinstance(label, dict) and label.get("name")
    ]
    body = (data.get("body") or "").strip()
    if len(body) > 1800:
        body = body[:1800] + "\n\n... (摘要已截断)"

    lines = [
        "# GitHub Issue 详情",
        f"Repo: {repo}",
        f"Issue: #{issue_number}",
        f"标题: {title}",
        f"状态: {state}" + (f" ({state_reason})" if state_reason else ""),
        f"作者: {author}",
        f"创建时间: {data.get('created_at') or 'unknown'}",
        f"更新时间: {data.get('updated_at') or 'unknown'}",
        f"评论数: {data.get('comments', 0)}",
    ]
    if labels:
        lines.append(f"Labels: {', '.join(labels)}")
    html_url = data.get("html_url")
    if html_url:
        lines.append(f"链接: {html_url}")
    if body:
        lines.extend(["", body])
    return ToolResult(output="\n".join(lines))


def _format_code_search_output(
    *,
    query: str,
    full_query: str,
    limit: int,
    data: dict[str, Any],
) -> ToolResult:
    items = data.get("items", []) or []
    total = data.get("total_count", 0)
    if not items:
        return ToolResult(
            output=(
                f"未找到与「{query}」匹配的代码。"
                f"\n搜索 query: `{full_query}`"
                "\n建议：缩短关键词 / 去掉 path 限制 / 改用 org 范围。"
            )
        )

    lines = [
        "# GitHub Code Search 结果",
        f"Query: `{full_query}`",
        f"Total matches: {total}（仅显示前 {min(len(items), limit)}）",
        "",
    ]
    for item in items[:limit]:
        repo_full = (item.get("repository") or {}).get("full_name", "?")
        file_path = item.get("path", "?")
        html_url = item.get("html_url", "")
        score = item.get("score", 0)
        lines.append(f"## {repo_full} · `{file_path}`")
        if html_url:
            lines.append(f"- 链接: {html_url}")
        lines.append(f"- 相关性 score: {score:.2f}")
        text_matches = item.get("text_matches") or []
        for match in text_matches[:3]:
            fragment = (match.get("fragment") or "").strip()
            if not fragment:
                continue
            if len(fragment) > 600:
                fragment = fragment[:600] + "…"
            lines.append("```")
            lines.append(fragment)
            lines.append("```")
        lines.append("")
    return ToolResult(output="\n".join(lines))


class GithubSearchPrs(Tool):
    name = "github_search_prs"
    description = "搜索 GitHub pull requests，用于确认某个问题是否已有修复 PR"
    parameters = {
        "type": "object",
        "properties": {
            "repo": {
                "type": "string",
                "description": "仓库全名，例如 langgenius/dify",
            },
            "query": {
                "type": "string",
                "description": "PR 搜索关键词，可包含错误信息、模块名、接口路径",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 10,
                "default": 5,
                "description": "返回结果数量，默认 5",
            },
        },
        "required": ["repo", "query"],
    }

    group = "source_code"
    max_output_tokens = 4000

    async def execute(self, args: dict[str, Any], session: AgentSession) -> ToolResult:
        repo = _coerce_required_string_arg(args, "repo")
        if isinstance(repo, ToolResult):
            return repo
        query = _coerce_required_string_arg(args, "query")
        if isinstance(query, ToolResult):
            return query
        limit = _coerce_int_arg(args, "limit", 5, minimum=1, maximum=10)
        if isinstance(limit, ToolResult):
            return limit
        if not repo or not query:
            return ToolResult(success=False, error="repo 和 query 不能为空")

        search_query = f"repo:{repo} is:pr {query}"
        payload = await call_github_tool(
            self.name,
            {
                "repo": repo,
                "query": query,
                "limit": limit,
                "search_query": search_query,
            },
        )
        if not payload.get("success"):
            return _mcp_failure(payload)
        if output := _payload_output(payload):
            return ToolResult(output=output)
        return _format_pr_search_output(
            repo=repo,
            query=query,
            limit=limit,
            data=_payload_data(payload),
        )


class GithubGetPr(Tool):
    name = "github_get_pr"
    description = "读取单个 GitHub PR 的状态、摘要与链接"
    parameters = {
        "type": "object",
        "properties": {
            "repo": {
                "type": "string",
                "description": "仓库全名，例如 langgenius/dify",
            },
            "pr_number": {
                "type": "integer",
                "description": "PR 编号",
            },
        },
        "required": ["repo", "pr_number"],
    }

    group = "source_code"

    async def execute(self, args: dict[str, Any], session: AgentSession) -> ToolResult:
        repo = _coerce_required_string_arg(args, "repo")
        if isinstance(repo, ToolResult):
            return repo
        pr_number = _coerce_int_arg(args, "pr_number")
        if isinstance(pr_number, ToolResult):
            return pr_number
        if not repo:
            return ToolResult(success=False, error="repo 不能为空")

        payload = await call_github_tool(
            self.name,
            {"repo": repo, "pr_number": pr_number},
        )
        if not payload.get("success"):
            return _mcp_failure(payload)
        if output := _payload_output(payload):
            return ToolResult(output=output)
        return _format_pr_detail_output(
            repo=repo,
            pr_number=pr_number,
            data=_payload_data(payload),
        )


class GithubSearchIssues(Tool):
    name = "github_search_issues"
    description = (
        "搜索 GitHub Issues，用于核对已知 bug、回归报告、已知限制和维护者结论。"
        "历史 Issue 只是候选证据；仍需核对当前版本和当前工单运行时事实。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "repo": {
                "type": "string",
                "description": "仓库全名，例如 langgenius/dify",
            },
            "query": {
                "type": "string",
                "description": "Issue 搜索关键词，可包含错误信息、模块名或行为描述",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 10,
                "default": 5,
                "description": "返回结果数量，默认 5",
            },
        },
        "required": ["repo", "query"],
    }

    group = "source_code"
    max_output_tokens = 4000

    async def execute(self, args: dict[str, Any], session: AgentSession) -> ToolResult:
        repo = _coerce_required_string_arg(args, "repo")
        if isinstance(repo, ToolResult):
            return repo
        query = _coerce_required_string_arg(args, "query")
        if isinstance(query, ToolResult):
            return query
        limit = _coerce_int_arg(args, "limit", 5, minimum=1, maximum=10)
        if isinstance(limit, ToolResult):
            return limit
        if not repo or not query:
            return ToolResult(success=False, error="repo 和 query 不能为空")

        payload = await call_github_tool(
            self.name,
            {"repo": repo, "query": query, "limit": limit},
        )
        if not payload.get("success"):
            return _mcp_failure(payload)
        if output := _payload_output(payload):
            return ToolResult(output=output)
        return _format_issue_search_output(
            repo=repo,
            query=query,
            limit=limit,
            data=_payload_data(payload),
        )


class GithubGetIssue(Tool):
    name = "github_get_issue"
    description = (
        "读取单个 GitHub Issue 的状态、标签、时间、正文和链接；"
        "用于验证搜索命中的候选 Issue，而不是直接证明当前生产根因。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "repo": {
                "type": "string",
                "description": "仓库全名，例如 langgenius/dify",
            },
            "issue_number": {
                "type": "integer",
                "description": "Issue 编号",
            },
        },
        "required": ["repo", "issue_number"],
    }

    group = "source_code"
    max_output_tokens = 4000

    async def execute(self, args: dict[str, Any], session: AgentSession) -> ToolResult:
        repo = _coerce_required_string_arg(args, "repo")
        if isinstance(repo, ToolResult):
            return repo
        issue_number = _coerce_int_arg(args, "issue_number")
        if isinstance(issue_number, ToolResult):
            return issue_number
        if not repo:
            return ToolResult(success=False, error="repo 不能为空")

        payload = await call_github_tool(
            self.name,
            {"repo": repo, "issue_number": issue_number},
        )
        if not payload.get("success"):
            return _mcp_failure(payload)
        if output := _payload_output(payload):
            return ToolResult(output=output)
        return _format_issue_detail_output(
            repo=repo,
            issue_number=issue_number,
            data=_payload_data(payload),
        )


class GithubCodeSearch(Tool):
    """远程在 GitHub 代码库里搜源码，不需要本地 clone。

    覆盖那些没在服务器上 clone 的仓库（dify-plugins / 各插件仓 /
    dify-cloud 等）。对找 Lark Bitable / Notion / Google Drive / Slack
    各种第三方数据源 / 工具插件的认证逻辑、报错抛出点特别有用。

    GitHub Code Search API 限速：authenticated 30 req/min, anon 10/min。
    """

    name = "github_code_search"
    description = (
        "远程在 GitHub 上搜源码（不依赖本地 clone）。"
        "用于查 langgenius org 内 dify 主仓 + dify-plugins + 各插件仓 + "
        "dify-cloud 等不在本地的代码。"
        "返回匹配文件路径 + 文件链接 + 命中代码片段。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "搜索关键词。可以是错误信息片段、函数名、字符串字面量。"
                    "不要写完整 URL 或太长的句子，关键词越精准越好。"
                ),
            },
            "scope": {
                "type": "string",
                "description": (
                    "搜索范围。两种写法：\n"
                    "  - org 名（默认 'langgenius'，覆盖整个组织）\n"
                    "  - 'owner/repo' 格式（精确到一个仓库，例如 "
                    "'langgenius/dify-plugins'）"
                ),
                "default": "langgenius",
            },
            "language": {
                "type": "string",
                "description": "可选：限定语言，如 'python' / 'typescript' / 'go'",
            },
            "path": {
                "type": "string",
                "description": "可选：限定路径前缀，如 'core/tools' / 'plugins/tools/lark'",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 30,
                "default": 10,
                "description": "返回结果数量（1-30）",
            },
        },
        "required": ["query"],
    }

    group = "source_code"
    max_output_tokens = 5000

    async def execute(self, args: dict[str, Any], session: AgentSession) -> ToolResult:
        query = _coerce_required_string_arg(args, "query")
        if isinstance(query, ToolResult):
            return query
        if not query:
            return ToolResult(success=False, error="query 不能为空")

        scope = _coerce_optional_string_arg(args, "scope")
        if isinstance(scope, ToolResult):
            return scope
        scope = scope or "langgenius"
        language = _coerce_optional_string_arg(args, "language")
        if isinstance(language, ToolResult):
            return language
        path = _coerce_optional_string_arg(args, "path")
        if isinstance(path, ToolResult):
            return path
        limit = _coerce_int_arg(args, "limit", 10, minimum=1, maximum=30)
        if isinstance(limit, ToolResult):
            return limit

        # 拼 GitHub search 的 qualifier。scope 同时支持 org 名和 owner/repo 格式
        qualifiers = [query]
        if "/" in scope:
            qualifiers.append(f"repo:{scope}")
        elif scope:
            qualifiers.append(f"org:{scope}")
        if language:
            qualifiers.append(f"language:{language}")
        if path:
            qualifiers.append(f"path:{path}")
        full_query = " ".join(qualifiers)

        payload = await call_github_tool(
            self.name,
            {
                "query": query,
                "scope": scope,
                "language": language,
                "path": path,
                "limit": limit,
                "full_query": full_query,
            },
        )
        if not payload.get("success"):
            return _mcp_failure(payload)
        if output := _payload_output(payload):
            return ToolResult(output=output)
        return _format_code_search_output(
            query=query,
            full_query=full_query,
            limit=limit,
            data=_payload_data(payload),
        )
