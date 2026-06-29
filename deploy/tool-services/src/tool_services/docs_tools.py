from __future__ import annotations

import re
import time
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlparse

import httpx

from .mcp import failure, optional_int, optional_str, required_str, success
from .settings import Settings

INDEX_URLS = {
    "official_docs_enterprise": "https://enterprise-docs.dify.ai/llms.txt",
    "official_docs_community": "https://docs.dify.ai/llms.txt",
}
ALLOWED_HOSTS = {"enterprise-docs.dify.ai", "docs.dify.ai"}
MAX_READ_CHARS = 18_000

TOOLS = [
    {
        "name": "official_docs_search",
        "description": "Search Dify official docs indexes.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "source": {"type": "string", "default": "auto"},
                "limit": {"type": "integer", "default": 5},
            },
            "required": ["query"],
        },
    },
    {
        "name": "official_docs_read",
        "description": "Read a Dify official docs page.",
        "inputSchema": {
            "type": "object",
            "properties": {"url": {"type": "string"}},
            "required": ["url"],
        },
    },
]


@dataclass(frozen=True)
class Entry:
    title: str
    url: str
    description: str
    source: str


_INDEX_CACHE: dict[str, tuple[float, list[Entry]]] = {}


async def handle(name: str, arguments: dict[str, Any], settings: Settings) -> dict[str, Any]:
    try:
        if name == "official_docs_search":
            query = required_str(arguments, "query")
            source = optional_str(arguments, "source", "auto") or "auto"
            limit = optional_int(arguments, "limit", 5, minimum=1, maximum=10)
            entries: list[Entry] = []
            for item in _resolve_sources(source):
                index = await _load_index(item, settings)
                entries.extend(_search(index, query=query, limit=limit))
            deduped = _dedupe(entries)[:limit]
            return success({"results": [entry.__dict__ for entry in deduped]})

        if name == "official_docs_read":
            url = required_str(arguments, "url")
            if not _allowed(url):
                return failure("URL_NOT_ALLOWED", "only Dify official docs hosts are allowed")
            text = await _read_url(url, settings)
            return success({"url": url, "content": text})

        return failure("UNKNOWN_TOOL", f"unknown tool: {name}")
    except ValueError as e:
        return failure("BAD_ARGUMENT", str(e))
    except httpx.HTTPStatusError as e:
        return failure("DOCS_HTTP_ERROR", f"HTTP {e.response.status_code}: {e.response.text[:500]}")
    except Exception as e:  # noqa: BLE001
        return failure("INTERNAL_ERROR", str(e))


async def _load_index(source: str, settings: Settings) -> list[Entry]:
    now = time.monotonic()
    cached = _INDEX_CACHE.get(source)
    if cached and cached[0] > now:
        return cached[1]
    async with httpx.AsyncClient(
        timeout=settings.docs_timeout_seconds,
        follow_redirects=True,
        trust_env=False,
    ) as client:
        resp = await client.get(INDEX_URLS[source])
        resp.raise_for_status()
    entries = _parse_llms(resp.text, source=source)
    _INDEX_CACHE[source] = (now + max(0.0, settings.docs_cache_ttl_seconds), entries)
    return entries


def _parse_llms(content: str, *, source: str) -> list[Entry]:
    pattern = re.compile(r"^- \[(?P<title>.+?)\]\((?P<url>https?://[^)]+)\)(?:: (?P<desc>.*))?$")
    entries: list[Entry] = []
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line.startswith("- ["):
            continue
        match = pattern.match(line)
        if not match:
            continue
        entries.append(
            Entry(
                title=match.group("title").strip(),
                url=match.group("url").strip(),
                description=(match.group("desc") or "").strip(),
                source=source,
            )
        )
    return entries


def _search(entries: list[Entry], *, query: str, limit: int) -> list[Entry]:
    normalized = query.casefold().strip()
    tokens = [token for token in re.split(r"[\s/,_:.-]+", normalized) if token]
    scored: list[tuple[int, Entry]] = []
    for entry in entries:
        title = entry.title.casefold()
        desc = entry.description.casefold()
        url = entry.url.casefold()
        score = 0
        if normalized in title:
            score += 15
        if normalized in desc:
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


async def _read_url(url: str, settings: Settings) -> str:
    async with httpx.AsyncClient(
        timeout=settings.docs_timeout_seconds,
        follow_redirects=True,
        trust_env=False,
    ) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        content_type = resp.headers.get("content-type", "")
        text = resp.text
    if "html" in content_type.lower():
        text = _strip_html(text)
    text = text.strip()
    if len(text) > MAX_READ_CHARS:
        text = text[:MAX_READ_CHARS] + "\n\n... (内容过长，已截断)"
    return text


def _resolve_sources(source: str) -> list[str]:
    if source in INDEX_URLS:
        return [source]
    return list(INDEX_URLS)


def _dedupe(entries: list[Entry]) -> list[Entry]:
    seen: set[str] = set()
    out: list[Entry] = []
    for entry in entries:
        if entry.url in seen:
            continue
        seen.add(entry.url)
        out.append(entry)
    return out


def _allowed(url: str) -> bool:
    host = urlparse(url).hostname or ""
    return host in ALLOWED_HOSTS


class _HTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        value = data.strip()
        if value:
            self.parts.append(value)


def _strip_html(html: str) -> str:
    html = re.sub(r"(?is)<script.*?>.*?</script>", " ", html)
    html = re.sub(r"(?is)<style.*?>.*?</style>", " ", html)
    parser = _HTMLTextExtractor()
    parser.feed(html)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(parser.parts))
