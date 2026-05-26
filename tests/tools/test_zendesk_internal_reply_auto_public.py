"""B 路径测试：ZendeskInternalReply 发完后自动抽 Suggested Reply 段
同步发 public_reply 给客户。

覆盖：
- 抽段函数：英/中标题 / emoji 前缀标题 / 找不到 → 空
- reply_type 推断：含 "could you" → ask；否则 solution
- 集成：execute 发完 internal 后，调 send_public_reply 一次
- 兜底：没 Suggested Reply 段 → 只发 internal 不发 public（不抛）
- kill switch：send_public_reply 返回 False → 不抛，只记 log
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from zd_agent.tools.zendesk import (
    _extract_suggested_reply_section,
    _infer_reply_type,
)


# ---------- _extract_suggested_reply_section ----------

class TestExtract:
    def test_english_header(self) -> None:
        body = (
            "## Internal Analysis\n\nstuff\n\n"
            "## Suggested Reply\n\nHi, the answer is X.\n\n"
            "## Review Guide\n\nnext\n"
        )
        out = _extract_suggested_reply_section(body)
        assert out == "Hi, the answer is X."

    def test_chinese_header(self) -> None:
        body = (
            "## 内部分析\n\n啥\n\n"
            "## 客户回复建议\n\n您好，已为您处理。\n\n"
            "## 人工指引\n\n下一步\n"
        )
        out = _extract_suggested_reply_section(body)
        assert out == "您好，已为您处理。"

    def test_emoji_prefix(self) -> None:
        body = "## 💬 Suggested Reply\n\nbody here\n\n## 🔍 Internal Analysis\n\nx\n"
        out = _extract_suggested_reply_section(body)
        assert out == "body here"

    def test_no_header_empty(self) -> None:
        assert _extract_suggested_reply_section("just text") == ""

    def test_no_following_section(self) -> None:
        """Suggested Reply 是最后一段，没下一个 ## → 取到末尾"""
        body = "## Suggested Reply\n\nfinal answer here.\n"
        out = _extract_suggested_reply_section(body)
        assert out == "final answer here."

    def test_real_agent_output_emoji_no_markdown_prefix(self) -> None:
        """#2651 真实格式: 💬 Suggested Reply 裸 emoji header 没有 ## 前缀"""
        body = (
            "🤖 AI-generated reply for reference only.\n\n"
            "📋 Summary\n"
            "Type config-deploy\n"
            "Confidence 🟢 High\n\n"
            "💬 Suggested Reply\n\n"
            "Hi Ayaka,\n\n"
            "I checked the scheduled trigger records. The workflow fails "
            "with `Model is not configured`.\n\n"
            "Best regards,\nDify Support\n\n"
            "⚠️ Audit Warnings (potential hallucination)\n\n"
            "- Reply 引用 PR #29982 但 tool 调用结果里未见\n"
        )
        out = _extract_suggested_reply_section(body)
        # 必须抽到内容（不再返回空串）
        assert out
        assert "Hi Ayaka" in out
        assert "Model is not configured" in out
        # 不能漏到 Audit Warnings 段
        assert "Audit Warnings" not in out
        assert "PR #29982" not in out

    def test_real_format_followed_by_review_guide(self) -> None:
        body = (
            "📋 Summary\nstuff\n\n"
            "💬 Suggested Reply\n\nHi, please try X.\n\n"
            "🔍 Internal Analysis\n\nDeep dive...\n"
        )
        out = _extract_suggested_reply_section(body)
        assert out == "Hi, please try X."

    def test_real_format_followed_by_mention_footer(self) -> None:
        """agent body 末尾的 '_You can follow up by mentioning @yonggang' 不该被吞"""
        body = (
            "💬 Suggested Reply\n\nFix is to enable provider Y.\n\n"
            "_You can follow up by mentioning @yonggang in an internal note._\n"
        )
        out = _extract_suggested_reply_section(body)
        assert out == "Fix is to enable provider Y."
        assert "@yonggang" not in out


# ---------- _infer_reply_type ----------

class TestInferReplyType:
    @pytest.mark.parametrize("body", [
        "Could you share the error message?",
        "Please provide a screenshot.",
        "Could you confirm the workspace?",
        "请提供错误信息",
        "能否分享一下截图",
    ])
    def test_ask_signals_return_ask(self, body: str) -> None:
        assert _infer_reply_type(body) == "ask"

    @pytest.mark.parametrize("body", [
        "The fix is to update X to Y.",
        "您的订阅已成功创建。",
        "Try setting the flag to true.",
    ])
    def test_no_ask_signals_return_solution(self, body: str) -> None:
        assert _infer_reply_type(body) == "solution"


# ---------- 集成：execute 完整流程 ----------

@pytest.fixture
def _mock_client():
    client = MagicMock()
    client.reply_ticket = MagicMock(return_value={"ticket": {"id": 1}})
    return client


def _make_args(body: str) -> dict:
    return {
        "ticket_id": 1234,
        "body": body,
        "summary": {
            "problem_type": "usage-howto",
            "confidence": "high",
            "key_finding": "x",
            "needs_human": False,
        },
    }


def _make_session() -> MagicMock:
    s = MagicMock()
    s.ticket_id = 1234
    s.dry_run = False
    s.reply_draft = ""
    s.reply_sent = False
    s.tool_call_log = []
    s.context = None
    return s


class TestIntegration:
    @pytest.mark.asyncio
    async def test_solution_body_triggers_public_reply(self, _mock_client) -> None:
        from zd_agent.tools.zendesk import ZendeskInternalReply
        body = (
            "## Internal Analysis\n\nDB checked\n\n"
            "## Suggested Reply\n\n"
            "Hi Alice, please update setting X to Y in workspace settings.\n\n"
            "## Review Guide\n\nnext\n"
        )
        mock_send_pr = AsyncMock(return_value=(True, "ok"))

        async def _passthrough(fn, *args, **kw):
            return fn(*args, **kw)

        with (
            patch(
                "zd_agent.tools.zendesk._get_client", return_value=_mock_client,
            ),
            patch(
                "zd_agent.tools.zendesk._call_with_rate_limit_retry",
                side_effect=_passthrough,
            ),
            patch(
                "zd_agent.tools.zendesk_public_reply.send_public_reply",
                mock_send_pr,
            ),
        ):
            tool = ZendeskInternalReply()
            r = await tool.execute(_make_args(body), _make_session())

        # Internal Note 发了
        assert r.success
        assert "Internal Note" in r.output
        # 自动 public_reply 发了
        mock_send_pr.assert_called_once()
        kwargs = mock_send_pr.call_args.kwargs
        assert kwargs["ticket_id"] == 1234
        assert kwargs["reply_type"] == "solution"
        # body 内容是抽出来的 Suggested Reply 段
        assert "Hi Alice, please update setting X to Y" in kwargs["body"]
        assert "Internal Analysis" not in kwargs["body"]

    @pytest.mark.asyncio
    async def test_ask_signal_picks_ask_type(self, _mock_client) -> None:
        from zd_agent.tools.zendesk import ZendeskInternalReply
        body = (
            "## Suggested Reply\n\n"
            "Hi, could you share the error message you're seeing?\n"
        )
        mock_send_pr = AsyncMock(return_value=(True, "ok"))

        async def _passthrough(fn, *args, **kw):
            return fn(*args, **kw)

        with (
            patch("zd_agent.tools.zendesk._get_client", return_value=_mock_client),
            patch(
                "zd_agent.tools.zendesk._call_with_rate_limit_retry",
                side_effect=_passthrough,
            ),
            patch(
                "zd_agent.tools.zendesk_public_reply.send_public_reply",
                mock_send_pr,
            ),
        ):
            tool = ZendeskInternalReply()
            await tool.execute(_make_args(body), _make_session())

        assert mock_send_pr.call_args.kwargs["reply_type"] == "ask"

    @pytest.mark.asyncio
    async def test_no_suggested_reply_section_only_internal(
        self, _mock_client,
    ) -> None:
        """body 没 Suggested Reply 段 → public_reply 不调，只发 Internal"""
        from zd_agent.tools.zendesk import ZendeskInternalReply
        body = "## Internal Analysis\n\nFull analysis without reply section.\n"
        mock_send_pr = AsyncMock(return_value=(True, "ok"))

        async def _passthrough(fn, *args, **kw):
            return fn(*args, **kw)

        with (
            patch("zd_agent.tools.zendesk._get_client", return_value=_mock_client),
            patch(
                "zd_agent.tools.zendesk._call_with_rate_limit_retry",
                side_effect=_passthrough,
            ),
            patch(
                "zd_agent.tools.zendesk_public_reply.send_public_reply",
                mock_send_pr,
            ),
        ):
            tool = ZendeskInternalReply()
            r = await tool.execute(_make_args(body), _make_session())

        assert r.success
        mock_send_pr.assert_not_called()
        assert "no_suggested_reply_section" in r.output

    @pytest.mark.asyncio
    async def test_public_send_failure_does_not_break_internal(
        self, _mock_client,
    ) -> None:
        """public_reply 失败（kill switch 等）→ Internal Note 仍算成功"""
        from zd_agent.tools.zendesk import ZendeskInternalReply
        body = (
            "## Suggested Reply\n\nHere's the fix: X.\n"
        )
        mock_send_pr = AsyncMock(return_value=(False, "kill_switch_off"))

        async def _passthrough(fn, *args, **kw):
            return fn(*args, **kw)

        with (
            patch("zd_agent.tools.zendesk._get_client", return_value=_mock_client),
            patch(
                "zd_agent.tools.zendesk._call_with_rate_limit_retry",
                side_effect=_passthrough,
            ),
            patch(
                "zd_agent.tools.zendesk_public_reply.send_public_reply",
                mock_send_pr,
            ),
        ):
            tool = ZendeskInternalReply()
            r = await tool.execute(_make_args(body), _make_session())

        assert r.success
        assert "failed" in r.output

    @pytest.mark.asyncio
    async def test_public_send_exception_does_not_break(
        self, _mock_client,
    ) -> None:
        """public_reply 抛 exception → 不影响 Internal Note 已成功的 return"""
        from zd_agent.tools.zendesk import ZendeskInternalReply
        body = "## Suggested Reply\n\nFix.\n"

        async def _passthrough(fn, *args, **kw):
            return fn(*args, **kw)

        with (
            patch("zd_agent.tools.zendesk._get_client", return_value=_mock_client),
            patch(
                "zd_agent.tools.zendesk._call_with_rate_limit_retry",
                side_effect=_passthrough,
            ),
            patch(
                "zd_agent.tools.zendesk_public_reply.send_public_reply",
                AsyncMock(side_effect=RuntimeError("network burned")),
            ),
        ):
            tool = ZendeskInternalReply()
            r = await tool.execute(_make_args(body), _make_session())

        assert r.success
        assert "exception" in r.output
