from __future__ import annotations

from zd_agent.runtime.openai_compat import chat_completions_url


def test_chat_completions_url_uses_openai_default() -> None:
    assert chat_completions_url(None) == "https://api.openai.com/v1/chat/completions"


def test_chat_completions_url_appends_chat_completions_to_custom_base() -> None:
    assert (
        chat_completions_url("https://ark.cn-beijing.volces.com/api/coding/v3")
        == "https://ark.cn-beijing.volces.com/api/coding/v3/chat/completions"
    )


def test_chat_completions_url_preserves_full_endpoint() -> None:
    endpoint = "https://example.com/custom/chat/completions"
    assert chat_completions_url(endpoint) == endpoint
