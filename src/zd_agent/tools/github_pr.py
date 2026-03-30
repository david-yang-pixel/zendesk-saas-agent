"""GitHub PR 只读工具。"""

from __future__ import annotations

from typing import Any

import aiohttp

from zd_agent.config import EnvSettings
from zd_agent.session.agent_session import AgentSession
from zd_agent.tools.base import Tool, ToolResult

GITHUB_API_BASE = "https://api.github.com"


def _github_headers() -> dict[str, str]:
    env = EnvSettings()
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if env.github_token:
        headers["Authorization"] = f"Bearer {env.github_token}"
    return headers


async def _github_request(url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(
        timeout=timeout,
        headers=_github_headers(),
    ) as session:
        async with session.get(url, params=params) as resp:
            data = await resp.json()
            if resp.status >= 400:
                raise RuntimeError(f"GitHub API [{resp.status}]: {data}")
            return data


def _token_hint() -> str:
    env = EnvSettings()
    if env.github_token:
        return ""
    return " 未配置 `github_token`，匿名请求可能触发 GitHub rate limit。"


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

    async def execute(self, args: dict[str, Any], session: AgentSession) -> ToolResult:
        repo = str(args["repo"]).strip()
        query = str(args["query"]).strip()
        limit = max(1, min(int(args.get("limit", 5)), 10))
        if not repo or not query:
            return ToolResult(success=False, error="repo 和 query 不能为空")

        search_query = f"repo:{repo} is:pr {query}"
        url = f"{GITHUB_API_BASE}/search/issues"

        try:
            data = await _github_request(
                url,
                params={"q": search_query, "per_page": limit},
            )
        except Exception as e:
            return ToolResult(success=False, error=f"{e}.{_token_hint()}".strip())

        items = data.get("items", [])
        if not items:
            return ToolResult(output=f"未找到与「{query}」相关的 GitHub PR（repo: {repo}）")

        lines = [f"# GitHub PR 搜索结果", f"Repo: {repo}", f"Query: {query}", ""]
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

    async def execute(self, args: dict[str, Any], session: AgentSession) -> ToolResult:
        repo = str(args["repo"]).strip()
        pr_number = int(args["pr_number"])
        if not repo:
            return ToolResult(success=False, error="repo 不能为空")

        url = f"{GITHUB_API_BASE}/repos/{repo}/pulls/{pr_number}"
        try:
            data = await _github_request(url)
        except Exception as e:
            return ToolResult(success=False, error=f"{e}.{_token_hint()}".strip())

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
