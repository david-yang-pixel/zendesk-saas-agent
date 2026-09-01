from __future__ import annotations

import re

from zd_agent.casework.domain.models import TicketSnapshot


SUPPORTED_REPLY_LANGUAGES = frozenset({"en", "ja"})
_JAPANESE_KANA_RE = re.compile(r"[\u3040-\u30ff]")
_JAPANESE_SUPPORT_QUESTION_MARKERS = (
    "大丈夫",
    "可能",
    "対応しています",
    "対応できます",
)
_JAPANESE_CONTACT_MARKERS = ("送", "問い合わせ", "連絡", "書")


def normalize_reply_language(value: str) -> str:
    """Normalize a supported customer reply language, defaulting safely to English."""

    primary = str(value or "").strip().replace("_", "-").casefold().split("-", 1)[0]
    return primary if primary in SUPPORTED_REPLY_LANGUAGES else "en"


def resolve_ticket_reply_language(ticket: TicketSnapshot) -> str:
    """Resolve the reply language from current customer prose, then Zendesk locale."""

    bodies = (
        tuple(event.body for event in ticket.customer_messages)
        if ticket.customer_messages
        else ticket.questions
    )
    if bodies and _JAPANESE_KANA_RE.search(bodies[-1]):
        return "ja"
    return normalize_reply_language(ticket.locale)


def asks_whether_japanese_is_supported(text: str) -> bool:
    """Recognize the narrow policy question without invoking account investigation."""

    normalized = str(text or "").strip()
    return bool(
        "日本語" in normalized
        and any(marker in normalized for marker in _JAPANESE_CONTACT_MARKERS)
        and any(marker in normalized for marker in _JAPANESE_SUPPORT_QUESTION_MARKERS)
        and ("?" in normalized or "？" in normalized or normalized.endswith("か"))
    )


def japanese_support_confirmation(language: str) -> str:
    if language == "ja":
        return "はい、日本語でお問い合わせいただいて問題ありません。"
    return "Yes, you can contact Dify Support in Japanese."
