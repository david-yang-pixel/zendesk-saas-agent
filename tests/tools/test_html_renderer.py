"""Tests for html_renderer.render_internal_note (default behavior).

核心断言：
- 默认 needs_human=False（agent 不主动 opt-in 就是绿色 ✅，不弹红警告）
- needs_human=True 显式时才弹"⚠️ Low confidence — please review before sending"
- needs_human=False + confidence=medium 也算 safe（绿色），confidence 只影响 summary card
"""
from __future__ import annotations

from zd_agent.tools.html_renderer import render_internal_note, render_summary_card


def test_default_needs_human_treated_as_no_warning() -> None:
    """summary 没 needs_human 字段 → 不该弹红警告"""
    summary = {
        "problem_type": "usage-howto",
        "confidence": "high",
        "key_finding": "x",
        # 故意省略 needs_human
    }
    body = "## Suggested Reply\nHi, the answer is X.\n"
    html = render_internal_note(summary, body)
    assert "⚠️ Low confidence" not in html
    # summary card 该显示 "✅ No"（不需要人工）
    assert "✅ No" in html


def test_explicit_needs_human_true_shows_warning() -> None:
    summary = {
        "problem_type": "refund",
        "confidence": "high",
        "key_finding": "退款工单",
        "needs_human": True,
        "needs_human_reason": "退款金额需财务审批",
    }
    body = "## Suggested Reply\nHi, refund draft attached.\n"
    html = render_internal_note(summary, body)
    assert "⚠️ Low confidence" in html
    assert "⚠️ Yes" in html
    assert "退款金额需财务审批" in html


def test_needs_human_false_medium_confidence_still_safe() -> None:
    """needs_human=False + confidence=medium → 走 safe 路径（不弹警告）

    之前 is_safe 还要求 confidence=='high'，导致 medium 总是被红警告
    → agent 进而都不敢用 high。改完之后 medium 也 ok。
    """
    summary = {
        "problem_type": "platform_issue",
        "confidence": "medium",
        "key_finding": "前端 trace details 渲染问题",
        "needs_human": False,
    }
    body = "## Suggested Reply\nHi, this looks like a frontend bug.\n"
    html = render_internal_note(summary, body)
    assert "⚠️ Low confidence" not in html
    assert "✅ No" in html


def test_summary_card_renders_confidence() -> None:
    """confidence 字段单独通过 summary card 可见，不依赖 is_safe 判定"""
    summary = {
        "problem_type": "billing",
        "confidence": "medium",
        "key_finding": "test",
    }
    html = render_summary_card(summary)
    # 不强测具体颜色，但 medium 字符串应出现在 card 里
    assert "medium" in html.lower() or "🟡" in html


# ---------------------------------------------------------------------------
# P1-6 紧凑模式：confidence=high + needs_human=False → 省略 Internal Analysis
# ---------------------------------------------------------------------------

class TestCompactMode:
    def _body(self) -> str:
        return (
            "## Suggested Reply\nHi, the answer is X.\n"
            "## Internal Analysis\nDetailed reasoning here.\n"
            "## Review Guide\nNext steps for SE.\n"
        )

    def test_high_confidence_safe_skips_analysis_and_review(self) -> None:
        summary = {
            "problem_type": "usage-howto",
            "confidence": "high",
            "key_finding": "x",
            "needs_human": False,
        }
        html = render_internal_note(summary, self._body())
        # Suggested Reply 仍渲染
        assert "Suggested Reply" in html
        assert "the answer is X" in html
        # Internal Analysis + Review Guide 被压缩掉
        assert "Internal Analysis" not in html
        assert "Review Guide" not in html
        # 分隔线提示也省略
        assert "Detailed analysis below" not in html

    def test_medium_confidence_keeps_full(self) -> None:
        summary = {
            "problem_type": "platform_issue",
            "confidence": "medium",
            "key_finding": "x",
            "needs_human": False,
        }
        html = render_internal_note(summary, self._body())
        # 完整模式：3 段都在
        assert "Suggested Reply" in html
        assert "Internal Analysis" in html
        assert "Review Guide" in html

    def test_needs_human_keeps_full_even_high_confidence(self) -> None:
        """需人工审核 → 即使 high confidence 也保留详细分析（SE 真的要看）"""
        summary = {
            "problem_type": "refund",
            "confidence": "high",
            "key_finding": "x",
            "needs_human": True,
            "needs_human_reason": "退款需财务",
        }
        html = render_internal_note(summary, self._body())
        assert "Suggested Reply" in html
        assert "Internal Analysis" in html
        assert "Review Guide" in html
