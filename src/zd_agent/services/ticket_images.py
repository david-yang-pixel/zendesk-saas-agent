"""Shared Zendesk ticket image extraction helpers."""
from __future__ import annotations

import html
import re
from collections.abc import Iterable
from urllib.parse import urlparse

_HTML_IMG_RE = re.compile(
    r"""<img\b[^>]*\bsrc\s*=\s*(?:"([^"]+)"|'([^']+)'|([^\s>]+))""",
    re.IGNORECASE,
)
_MARKDOWN_IMG_RE = re.compile(
    r"""!\[[^\]]*\]\(\s*(?:<([^>]+)>|([^)\s]+))(?:\s+["'][^"']*["'])?\s*\)""",
    re.IGNORECASE,
)
_SC_ATTACHMENT_RE = re.compile(
    r"""https?://[^\s<>"')]+/sc/attachments/v2/[^\s<>"')]+/?""",
    re.IGNORECASE,
)
_IMAGE_URL_RE = re.compile(
    r"""https?://[^\s<>"')]+\.(?:png|jpe?g|gif|webp|bmp|svg)(?:\?[^\s<>"')]+)?""",
    re.IGNORECASE,
)


def _clean_url(url: str | None) -> str:
    cleaned = html.unescape((url or "").strip())
    return cleaned.strip("<>\"'").rstrip(").,;]")


def _dedupe(urls: Iterable[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in urls:
        url = _clean_url(raw)
        if not url or url in seen:
            continue
        seen.add(url)
        out.append(url)
    return out


def _url_looks_like_image(url: str) -> bool:
    return bool(_SC_ATTACHMENT_RE.search(url) or _IMAGE_URL_RE.search(url))


def extract_inline_image_urls(text: str | None) -> list[str]:
    """Extract image-like URLs from HTML, Markdown image syntax, and Zendesk uploads."""
    if not text:
        return []

    candidates: list[str] = []
    for match in _HTML_IMG_RE.finditer(text):
        candidates.append(next(group for group in match.groups() if group))
    for match in _MARKDOWN_IMG_RE.finditer(text):
        candidates.append(next(group for group in match.groups() if group))
    candidates.extend(match.group(0) for match in _SC_ATTACHMENT_RE.finditer(text))
    candidates.extend(match.group(0) for match in _IMAGE_URL_RE.finditer(text))
    return _dedupe(candidates)


def image_url_from_attachment(att: dict) -> str:
    ctype = (att.get("content_type") or att.get("type") or "").lower()
    url = (
        att.get("content_url")
        or att.get("mapped_content_url")
        or att.get("media_url")
        or att.get("url")
    )
    cleaned = _clean_url(url)
    if not cleaned:
        return ""
    if ctype:
        return cleaned if ctype == "image" or ctype.startswith("image/") else ""
    return cleaned if _url_looks_like_image(cleaned) else ""


def collect_image_urls_from_attachments(attachments: Iterable[dict] | None) -> list[str]:
    return _dedupe(
        image_url_from_attachment(att)
        for att in (attachments or [])
        if isinstance(att, dict)
    )


def filename_from_url(url: str, *, default: str = "image") -> str:
    path = urlparse(url).path.rstrip("/")
    name = path.rsplit("/", 1)[-1] if path else ""
    return name or default
