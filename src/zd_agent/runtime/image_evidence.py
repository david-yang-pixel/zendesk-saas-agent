"""Guard against stale no-image conclusions after later image evidence appears."""
from __future__ import annotations

import re
from typing import Any

_IMAGE_EVIDENCE_NOTICE = (
    "早期附件查询当时未发现图片（可能图片尚未到达）；"
    "当前会话已有客户图片证据，请以最新图片状态为准"
)

_STALE_NO_IMAGE_PATTERNS = (
    re.compile(r"Zendesk\s*返回无附件或图片", re.IGNORECASE),
    re.compile(r"该工单没有附件或图片", re.IGNORECASE),
    re.compile(r"Zendesk\s+returned\s+no\s+attachments?\s+or\s+images?", re.IGNORECASE),
    re.compile(r"no\s+attachments?\s+or\s+images?", re.IGNORECASE),
)


def has_image_evidence(session: Any) -> bool:
    if getattr(session, "images_injected", False) is True:
        return True
    image_urls = getattr(session, "image_urls", None)
    return isinstance(image_urls, list) and len(image_urls) > 0


def guard_against_stale_no_image_claim(text: str, session: Any) -> str:
    if not text or not has_image_evidence(session):
        return text

    guarded = text
    for pattern in _STALE_NO_IMAGE_PATTERNS:
        guarded = pattern.sub(_IMAGE_EVIDENCE_NOTICE, guarded)
    return guarded


def guard_text_values_against_stale_no_image_claim(value: Any, session: Any) -> Any:
    if isinstance(value, str):
        return guard_against_stale_no_image_claim(value, session)
    if isinstance(value, dict):
        return {
            key: guard_text_values_against_stale_no_image_claim(item, session)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [
            guard_text_values_against_stale_no_image_claim(item, session)
            for item in value
        ]
    return value
