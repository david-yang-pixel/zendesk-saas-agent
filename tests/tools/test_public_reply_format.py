from __future__ import annotations

import pytest

from zd_agent.tools.public_reply_format import _append_footer


EXPECTED_FOOTER = (
    "This reply was sent by Dify AI Agent. If this does not resolve your issue, "
    "reply \u201chuman\u201d. A human support engineer will respond as soon as possible "
    "during support hours, 9:00 AM\u20136:00 PM Beijing Time (UTC+8)."
)


@pytest.mark.parametrize("reply_type", ["ask", "solution", "handoff", "closed"])
def test_every_public_reply_uses_the_same_required_ai_footer(reply_type: str) -> None:
    rendered = _append_footer("Customer-visible body.", reply_type)

    assert rendered.endswith(EXPECTED_FOOTER)
    assert rendered.count("This reply was sent by Dify AI Agent.") == 1


def test_required_ai_footer_is_idempotent() -> None:
    once = _append_footer("Customer-visible body.", "solution")

    assert _append_footer(once, "solution") == once
