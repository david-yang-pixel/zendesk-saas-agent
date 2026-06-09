"""官方在线文档工具：支持索引搜索与在线阅读。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlparse

import httpx

from zd_agent.session.agent_session import AgentSession
from zd_agent.tools.base import Tool, ToolResult

INDEX_URLS = {
    "official_docs_enterprise": "https://enterprise-docs.dify.ai/llms.txt",
    "official_docs_community": "https://docs.dify.ai/llms.txt",
}

ALLOWED_HOSTS = {
    "enterprise-docs.dify.ai",
    "docs.dify.ai",
}

MAX_READ_CHARS = 18000
_INDEX_CACHE: dict[str, list["OfficialDocEntry"]] = {}


def _coerce_limit(value: Any, *, default: int = 5, maximum: int = 10) -> int | ToolResult:
    try:
        limit = int(default if value is None else value)
    except (TypeError, ValueError):
        return ToolResult(success=False, error="limit must be an integer")
    return max(1, min(limit, maximum))


@dataclass(frozen=True)
class OfficialDocEntry:
    """单篇官方文档索引条目。"""

    title: str
    url: str
    description: str
    source: str


def parse_llms_index(content: str, *, source: str) -> list[OfficialDocEntry]:
    """解析 llms.txt 索引，抽取标题、URL 与简介。"""

    entries: list[OfficialDocEntry] = []
    pattern = re.compile(r"^- \[(?P<title>.+?)\]\((?P<url>https?://[^)]+)\)(?:: (?P<desc>.*))?$")

    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line.startswith("- ["):
            continue
        match = pattern.match(line)
        if not match:
            continue
        entries.append(
            OfficialDocEntry(
                title=match.group("title").strip(),
                url=match.group("url").strip(),
                description=(match.group("desc") or "").strip(),
                source=source,
            )
        )

    return entries


def search_doc_entries(
    entries: list[OfficialDocEntry], *, query: str, limit: int = 5,
) -> list[OfficialDocEntry]:
    """在 llms.txt 条目中做轻量检索排序。"""

    normalized_query = query.casefold().strip()
    if not normalized_query:
        return entries[:limit]

    tokens = [token for token in re.split(r"[\s/,_:.-]+", normalized_query) if token]
    scored: list[tuple[int, OfficialDocEntry]] = []

    for entry in entries:
        title = entry.title.casefold()
        desc = entry.description.casefold()
        url = entry.url.casefold()
        score = 0

        if normalized_query in title:
            score += 15
        if normalized_query in desc:
            score += 10

        for token in tokens:
            if token in title:
                score += 6
            if token in desc:
                score += 3
            if token in url:
                score += 2

        if score > 0:
            scored.append((score, entry))

    scored.sort(key=lambda item: (-item[0], item[1].title))
    return [entry for _, entry in scored[:limit]]


class OfficialDocsSearch(Tool):
    name = "official_docs_search"
    description = (
        "搜索 Dify 官方在线文档索引（企业版 / 社区版），返回候选文档标题、URL 和摘要。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "搜索关键词，例如 SSO、Helm、环境变量、API",
            },
            "source": {
                "type": "string",
                "description": "限定文档来源：official_docs_enterprise、official_docs_community 或 auto",
            },
            "limit": {
                "type": "integer",
                "description": "返回结果数量，默认 5",
            },
        },
        "required": ["query"],
    }

    group = "knowledge"
    max_output_tokens = 4000

    async def execute(self, args: dict[str, Any], session: AgentSession) -> ToolResult:
        query = args["query"]
        source = args.get("source", "auto")
        limit = _coerce_limit(args.get("limit"))
        if isinstance(limit, ToolResult):
            return limit

        sources = _resolve_sources(source)
        results: list[OfficialDocEntry] = []
        for item in sources:
            entries = await _load_index(item)
            results.extend(search_doc_entries(entries, query=query, limit=limit))

        deduped = _dedupe_entries(results)
        if not deduped:
            return ToolResult(output=f"未在官方文档索引中找到与「{query}」相关的内容")

        lines = [f"# 官方文档搜索结果", f"查询: {query}", ""]
        for idx, entry in enumerate(deduped[:limit], start=1):
            lines.append(f"{idx}. [{entry.title}]({entry.url})")
            lines.append(f"   - 来源: {entry.source}")
            if entry.description:
                lines.append(f"   - 摘要: {entry.description}")
        return ToolResult(output="\n".join(lines))


class OfficialDocsRead(Tool):
    name = "official_docs_read"
    description = "在线读取指定的 Dify 官方文档页面内容，仅允许 docs.dify.ai 与 enterprise-docs.dify.ai"
    parameters = {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "文档 URL，通常来自 official_docs_search 的结果",
            },
        },
        "required": ["url"],
    }

    group = "knowledge"

    async def execute(self, args: dict[str, Any], session: AgentSession) -> ToolResult:
        url = args["url"]
        if not _is_allowed_url(url):
            return ToolResult(success=False, error="仅允许读取 Dify 官方文档站点")

        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            response = await client.get(url)
            response.raise_for_status()
            content_type = response.headers.get("content-type", "")
            text = response.text

        if "html" in content_type.lower():
            text = _strip_html(text)

        text = text.strip()
        if len(text) > MAX_READ_CHARS:
            text = text[:MAX_READ_CHARS] + "\n\n... (内容过长，已截断)"

        return ToolResult(output=f"# 官方文档\nURL: {url}\n\n{text}")


async def _load_index(source: str) -> list[OfficialDocEntry]:
    if source in _INDEX_CACHE:
        return _INDEX_CACHE[source]

    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
        response = await client.get(INDEX_URLS[source])
        response.raise_for_status()
        entries = parse_llms_index(response.text, source=source)
        _INDEX_CACHE[source] = entries
        return entries


def _resolve_sources(source: str) -> list[str]:
    if source in INDEX_URLS:
        return [source]
    return list(INDEX_URLS.keys())


def _dedupe_entries(entries: list[OfficialDocEntry]) -> list[OfficialDocEntry]:
    seen: set[str] = set()
    deduped: list[OfficialDocEntry] = []
    for entry in entries:
        if entry.url in seen:
            continue
        seen.add(entry.url)
        deduped.append(entry)
    return deduped


def _is_allowed_url(url: str) -> bool:
    host = urlparse(url).hostname or ""
    return host in ALLOWED_HOSTS


class _HTMLTextExtractor(HTMLParser):
    """简易 HTML 文本提取器。"""

    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []

    def handle_data(self, data: str) -> None:
        value = data.strip()
        if value:
            self._parts.append(value)

    def get_text(self) -> str:
        return "\n".join(self._parts)


def _strip_html(html: str) -> str:
    html = re.sub(r"(?is)<script.*?>.*?</script>", " ", html)
    html = re.sub(r"(?is)<style.*?>.*?</style>", " ", html)
    parser = _HTMLTextExtractor()
    parser.feed(html)
    text = parser.get_text()
    return re.sub(r"\n{3,}", "\n\n", text)
