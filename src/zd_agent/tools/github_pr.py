"""GitHub PR + Code 搜索只读工具。"""

from __future__ import annotations

from typing import Any

import aiohttp

from zd_agent.config import EnvSettings
from zd_agent.session.agent_session import AgentSession
from zd_agent.tools.base import Tool, ToolResult

GITHUB_API_BASE = "https://api.github.com"


def _github_headers(*, text_match: bool = False) -> dict[str, str]:
    env = EnvSettings()
    accept = (
        "application/vnd.github.text-match+json" if text_match
        else "application/vnd.github+json"
    )
    headers = {
        "Accept": accept,
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if env.github_token:
        headers["Authorization"] = f"Bearer {env.github_token}"
    return headers


async def _github_request(
    url: str,
    params: dict[str, Any] | None = None,
    *,
    text_match: bool = False,
) -> dict[str, Any]:
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(
        timeout=timeout,
        headers=_github_headers(text_match=text_match),
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

    group = "source_code"
    max_output_tokens = 4000

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

    group = "source_code"

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
        query = str(args["query"]).strip()
        if not query:
            return ToolResult(success=False, error="query 不能为空")

        scope = str(args.get("scope") or "langgenius").strip()
        language = (args.get("language") or "").strip()
        path = (args.get("path") or "").strip()
        limit = max(1, min(int(args.get("limit", 10)), 30))

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

        url = f"{GITHUB_API_BASE}/search/code"
        try:
            data = await _github_request(
                url,
                params={"q": full_query, "per_page": limit},
                text_match=True,
            )
        except Exception as e:
            return ToolResult(success=False, error=f"{e}.{_token_hint()}".strip())

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
        for it in items[:limit]:
            repo_full = (it.get("repository") or {}).get("full_name", "?")
            file_path = it.get("path", "?")
            html_url = it.get("html_url", "")
            score = it.get("score", 0)
            lines.append(f"## {repo_full} · `{file_path}`")
            if html_url:
                lines.append(f"- 链接: {html_url}")
            lines.append(f"- 相关性 score: {score:.2f}")
            # text_matches 是行级命中片段（auth 时启用 text_match accept）
            tm = it.get("text_matches") or []
            for m in tm[:3]:
                fragment = (m.get("fragment") or "").strip()
                if not fragment:
                    continue
                # 截断单段 fragment 避免一条 result 占太多 token
                if len(fragment) > 600:
                    fragment = fragment[:600] + "…"
                lines.append("```")
                lines.append(fragment)
                lines.append("```")
            lines.append("")
        return ToolResult(output="\n".join(lines))
