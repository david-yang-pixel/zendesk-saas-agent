"""Customer nudge: language-aware canned message asking the customer
to describe their issue in detail.

Heuristic over the customer Name (from formResponse) — we cover CJK / Japanese /
fallback English. This is intentionally stupid-simple; we tolerate misclassifying
e.g. mixed-script names by falling back to English on ambiguity.
"""
from __future__ import annotations

import re
from typing import Literal

Lang = Literal["zh", "ja", "en"]


_HIRAGANA_KATAKANA = re.compile(r"[぀-ヿ]")
_HAN_CJK = re.compile(r"[一-鿿]")


def detect_language(name: str | None) -> Lang:
    """Pick zh / ja / en for the customer's name. English is the default."""
    if not name:
        return "en"
    s = name.strip()
    if not s:
        return "en"
    # Japanese kana → ja (more specific than CJK han)
    if _HIRAGANA_KATAKANA.search(s):
        return "ja"
    # CJK han characters → zh (could be Japanese kanji-only, but we err to zh)
    if _HAN_CJK.search(s):
        return "zh"
    return "en"


_NUDGE_TEXTS: dict[Lang, str] = {
    "zh": (
        "您好，我是 Dify 客服助理。"
        "为了帮您快速排查问题，请详细描述您遇到的具体情况，包括：\n"
        "1. 具体的报错信息或异常表现\n"
        "2. 您的 Workspace ID（已通过表单提供，确认即可）\n"
        "3. 复现步骤或操作流程\n"
        "如果是截图，请确保截图清晰能看到错误内容。"
    ),
    "ja": (
        "こんにちは、Dify サポートのアシスタントです。"
        "お困りの内容を素早くお調べするために、以下の情報を具体的に教えていただけますか：\n"
        "1. エラーメッセージや具体的な症状\n"
        "2. Workspace ID（フォームで既にご提供済みであれば再確認のみで OK）\n"
        "3. 再現手順または操作フロー\n"
        "スクリーンショットの場合は、エラー内容がはっきり見えるものをお願いします。"
    ),
    "en": (
        "Hi! I'm the Dify support assistant. "
        "To help investigate quickly, could you please provide more detail:\n"
        "1. The exact error message or specific behavior you're seeing\n"
        "2. Your Workspace ID (already provided in the form — just confirm)\n"
        "3. Steps to reproduce or what you were trying to do\n"
        "If you're sharing a screenshot, please make sure the error is clearly visible."
    ),
}


def nudge_message(*, customer_name: str | None) -> tuple[Lang, str]:
    """Return (language, message body) for the public nudge."""
    lang = detect_language(customer_name)
    return lang, _NUDGE_TEXTS[lang]
