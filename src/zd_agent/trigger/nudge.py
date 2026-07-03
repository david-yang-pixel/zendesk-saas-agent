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


_NUDGE_TEXT = (
    "Hi, thanks for reaching out. Could you please let us know what you need help with? "
    "For example, is it related to billing/refund, feature usage or how-to guidance, "
    "account/subscription changes, workflows, knowledge base, API, or an error/bug?"
)


def nudge_message(*, customer_name: str | None) -> tuple[Lang, str]:
    return "en", _NUDGE_TEXT
