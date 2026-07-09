from __future__ import annotations

from typing import Final

DEFAULT_OPENAI_BASE_URL: Final = "https://api.openai.com/v1"
CHAT_COMPLETIONS_PATH: Final = "/chat/completions"


def chat_completions_url(base_url: str | None) -> str:
    normalized = (base_url or DEFAULT_OPENAI_BASE_URL).strip().rstrip("/")
    if normalized.endswith(CHAT_COMPLETIONS_PATH):
        return normalized
    return f"{normalized}{CHAT_COMPLETIONS_PATH}"
