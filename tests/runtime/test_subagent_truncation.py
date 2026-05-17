"""Tests for subagent return truncation (P1-4).

防止 subagent 返回 20-50K 字符堆积到父 loop history → 触发 token 超预算。
"""
from __future__ import annotations

from zd_agent.runtime.subagent import (
    _MAX_SUBAGENT_OUTPUT_CHARS,
    _truncate_subagent_output,
)


class TestTruncate:
    def test_short_unchanged(self) -> None:
        text = "short conclusion"
        out, truncated = _truncate_subagent_output(text)
        assert out == text
        assert truncated is False

    def test_at_boundary_unchanged(self) -> None:
        text = "a" * _MAX_SUBAGENT_OUTPUT_CHARS
        out, truncated = _truncate_subagent_output(text)
        assert out == text
        assert truncated is False

    def test_over_boundary_truncated(self) -> None:
        text = "x" * (_MAX_SUBAGENT_OUTPUT_CHARS + 1)
        out, truncated = _truncate_subagent_output(text)
        assert truncated is True
        # 截断后长度小于原长度
        assert len(out) < len(text)
        # 带截断标记
        assert "已截断" in out
        # 原长度信息在标记里
        assert str(len(text)) in out

    def test_large_truncated_to_about_3600_chars(self) -> None:
        """50K 字符应被截到 ~3600 字符（90% of 4000）+ 截断提示"""
        text = "y" * 50000
        out, truncated = _truncate_subagent_output(text)
        assert truncated is True
        # 应该 < 4000 字符（不含 marker 后的提示）
        # 总长度允许略超（因为 marker 文字本身有 100+ 字符）
        assert len(out) < _MAX_SUBAGENT_OUTPUT_CHARS + 200

    def test_keeps_head_not_tail(self) -> None:
        """截断保留前部，因为子 agent 结论通常在开头"""
        prefix = "IMPORTANT_CONCLUSION_HEAD"
        text = prefix + ("z" * 10000)
        out, truncated = _truncate_subagent_output(text)
        assert truncated is True
        assert prefix in out
