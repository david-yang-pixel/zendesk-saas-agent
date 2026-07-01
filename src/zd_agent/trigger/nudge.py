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
        "请告诉我这次需要帮助的具体事项，例如退款/账单、功能使用、账号/订阅、Workflow、Knowledge Base、API，或遇到的报错。\n"
        "为了更快处理，请尽量补充：\n"
        "1. 相关页面、功能、账单或订阅事项\n"
        "2. 具体金额、时间、错误文本或截图（如适用）\n"
        "3. 您期望的结果和当前实际情况"
    ),
    "ja": (
        "こんにちは、Dify サポートのアシスタントです。"
        "返金/請求、機能の使い方、アカウント/契約、Workflow、Knowledge Base、API、エラーなど、今回サポートが必要な内容を教えてください。\n"
        "できれば以下も共有してください：\n"
        "1. 関連するページ、機能、請求、または契約内容\n"
        "2. 金額、時刻、エラーメッセージ、スクリーンショット（該当する場合）\n"
        "3. 期待していた結果と現在の状況"
    ),
    "en": (
        "Hi! I'm the Dify support assistant. "
        "What can I help you with today? For example: billing/refund, feature usage, account/subscription, Workflow, Knowledge Base, API, or an error you're seeing.\n"
        "To help us handle it faster, please share:\n"
        "1. The relevant page, feature, billing item, or subscription action\n"
        "2. Any amount, timestamp, error text, or screenshot if applicable\n"
        "3. What you expected and what happened instead"
    ),
}


def nudge_message(*, customer_name: str | None) -> tuple[Lang, str]:
    """Return (language, message body) for the public nudge."""
    lang = detect_language(customer_name)
    return lang, _NUDGE_TEXTS[lang]
