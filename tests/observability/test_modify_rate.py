"""Tests for observability.modify_rate."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from zd_agent.observability.modify_rate import (
    ModifyRateSample,
    _classify_similarity,
    _extract_suggested_reply,
    measure_ticket_modify_rate,
    summarize_samples,
)


# ---------------------------------------------------------------------------
# Unit: similarity classification + suggested-reply extraction
# ---------------------------------------------------------------------------


class TestClassifySimilarity:
    @pytest.mark.parametrize(
        "sim,expected",
        [
            (1.0, "as_is"),
            (0.9, "as_is"),
            (0.85, "as_is"),
            (0.8, "minor"),
            (0.5, "minor"),
            (0.49, "major"),
            (0.0, "major"),
        ],
    )
    def test_thresholds(self, sim: float, expected: str) -> None:
        assert _classify_similarity(sim) == expected


class TestExtractSuggestedReply:
    def test_english_header(self) -> None:
        body = (
            "🤖 AI-generated reply for reference only.\n\n"
            "## Suggested Reply\n\n"
            "Hi Alice, here's the fix.\n\n"
            "## Internal Analysis\n\nDeep dive...\n"
        )
        out = _extract_suggested_reply(body)
        assert out == "Hi Alice, here's the fix."

    def test_chinese_header(self) -> None:
        body = "## 客户回复建议\n\n您好，已为您处理。\n\n## 内部分析\n\n详细分析\n"
        out = _extract_suggested_reply(body)
        assert out == "您好，已为您处理。"

    def test_emoji_prefix_in_header(self) -> None:
        body = "## 💬 Suggested Reply\n\nBody here\n\n## 🔍 Internal Analysis\n\nx\n"
        out = _extract_suggested_reply(body)
        assert out == "Body here"

    def test_no_header_returns_empty(self) -> None:
        assert _extract_suggested_reply("just some text") == ""


# ---------------------------------------------------------------------------
# Integration: measure_ticket_modify_rate
# ---------------------------------------------------------------------------

AGENT_UID = 45433304089364


def _draft_body(reply_text: str = "Hi, the answer is X.") -> str:
    return (
        f"🤖 AI-generated reply for reference only.\n\n"
        f"## Suggested Reply\n\n{reply_text}\n\n"
        f"## Internal Analysis\n\nDeep stuff.\n"
    )


def _mock_mcp_call(comments: list[dict] | None = None, *, fail: bool = False):
    async def fake_call(name: str, arguments: dict):
        assert name == "get_ticket_comments"
        if fail:
            return {"success": False, "code": "ZENDESK_API_ERROR", "error": "zd 500"}
        return {"success": True, "data": {"comments": comments or []}}

    return fake_call


class TestMeasureTicketModifyRate:
    @pytest.mark.asyncio
    async def test_no_draft(self) -> None:
        mcp_call = _mock_mcp_call(
            [
                {"id": 1, "author_id": AGENT_UID, "public": False, "body": "no marker"},
            ]
        )
        with patch(
            "zd_agent.observability.modify_rate.call_zendesk_tool",
            side_effect=mcp_call,
        ):
            s = await measure_ticket_modify_rate(9999, AGENT_UID)
        assert s.classification == "no_draft"

    @pytest.mark.asyncio
    async def test_no_reply_after_draft(self) -> None:
        mcp_call = _mock_mcp_call(
            [
                {
                    "id": 1,
                    "author_id": AGENT_UID,
                    "public": False,
                    "body": _draft_body(),
                },
            ]
        )
        with patch(
            "zd_agent.observability.modify_rate.call_zendesk_tool",
            side_effect=mcp_call,
        ):
            s = await measure_ticket_modify_rate(9999, AGENT_UID)
        assert s.classification == "no_reply"
        assert s.similarity is None

    @pytest.mark.asyncio
    async def test_as_is_forward(self) -> None:
        """SE 原文 forward draft → similarity = 1.0 → as_is"""
        reply = "Hi, the answer is X."
        mcp_call = _mock_mcp_call(
            [
                {
                    "id": 1,
                    "author_id": AGENT_UID,
                    "public": False,
                    "body": _draft_body(reply),
                },
                {"id": 2, "author_id": AGENT_UID, "public": True, "body": reply},
            ]
        )
        with patch(
            "zd_agent.observability.modify_rate.call_zendesk_tool",
            side_effect=mcp_call,
        ):
            s = await measure_ticket_modify_rate(9999, AGENT_UID)
        assert s.classification == "as_is"
        assert s.similarity is not None and s.similarity >= 0.85

    @pytest.mark.asyncio
    async def test_minor_modification(self) -> None:
        """SE 加了点开头/结尾但主体保留 → minor (0.5 <= sim < 0.85)"""
        draft_reply = (
            "Hi Alice, your workflow run failed because the LLM provider "
            "returned a 429 rate limit. Please retry after 60s or switch "
            "to another provider in the workspace settings."
        )
        # 主体相同，只是加了称呼变化和一个补充句
        se_reply = (
            "Hi Alice, your workflow run failed because the LLM provider "
            "returned a 429 rate limit. Please retry after 60s or switch "
            "to another provider in the workspace settings. Let me know if "
            "you keep seeing this and we can look further."
        )
        mcp_call = _mock_mcp_call(
            [
                {
                    "id": 1,
                    "author_id": AGENT_UID,
                    "public": False,
                    "body": _draft_body(draft_reply),
                },
                {"id": 2, "author_id": AGENT_UID, "public": True, "body": se_reply},
            ]
        )
        with patch(
            "zd_agent.observability.modify_rate.call_zendesk_tool",
            side_effect=mcp_call,
        ):
            s = await measure_ticket_modify_rate(9999, AGENT_UID)
        # 该案例 similarity 应落在 [0.5, 0.85] 范围 → minor
        assert s.classification == "minor", (
            f"Expected minor, got {s.classification} with sim={s.similarity}"
        )

    @pytest.mark.asyncio
    async def test_major_rewrite(self) -> None:
        draft_reply = "Hi, please clarify which log type you want to export."
        se_reply = (
            "Hi Alice, audit logs can be exported via Settings → Audit Logs → "
            "Export CSV. Conversation logs need API access via /conversations."
        )
        mcp_call = _mock_mcp_call(
            [
                {
                    "id": 1,
                    "author_id": AGENT_UID,
                    "public": False,
                    "body": _draft_body(draft_reply),
                },
                {"id": 2, "author_id": AGENT_UID, "public": True, "body": se_reply},
            ]
        )
        with patch(
            "zd_agent.observability.modify_rate.call_zendesk_tool",
            side_effect=mcp_call,
        ):
            s = await measure_ticket_modify_rate(9999, AGENT_UID)
        assert s.classification == "major"
        assert s.similarity is not None and s.similarity < 0.5

    @pytest.mark.asyncio
    async def test_fetch_failure_returns_no_draft(self) -> None:
        with patch(
            "zd_agent.observability.modify_rate.call_zendesk_tool",
            side_effect=_mock_mcp_call(fail=True),
        ):
            s = await measure_ticket_modify_rate(9999, AGENT_UID)
        assert s.classification == "no_draft"


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


class TestSummarize:
    def test_empty(self) -> None:
        assert summarize_samples([]) == {"total": 0}

    def test_mixed(self) -> None:
        samples = [
            ModifyRateSample(1, "as_is", 0.95),
            ModifyRateSample(2, "as_is", 0.9),
            ModifyRateSample(3, "minor", 0.7),
            ModifyRateSample(4, "major", 0.3),
            ModifyRateSample(5, "no_reply", None),
        ]
        out = summarize_samples(samples)
        assert out["total"] == 5
        assert out["by_classification"]["as_is"] == 2
        assert out["by_classification"]["no_reply"] == 1
        assert out["as_is_rate"] == 0.4
        assert out["no_reply_rate"] == 0.2
        # avg_similarity 只算非 None 的 (0.95+0.9+0.7+0.3)/4 = 0.7125
        # Python round() banker's rounding 把 0.7125 → 0.713（5 进偶）
        assert out["avg_similarity"] == 0.713
