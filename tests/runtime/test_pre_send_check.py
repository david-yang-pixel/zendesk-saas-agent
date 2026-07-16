"""Tests for pre_send_check module (M5).

覆盖：
- is_substantive_heuristic 各种规则（image / UUID / URL / code / 长文本 / IGNORE）
- 灰区返回 None
- is_substantive_llm mocked（http 200 / http 错 / json 错）
- pre_send_check_or_abort 各种 fetch 后场景：
    - 无新消息 → continue
    - 1 条实质新消息 → abort
    - 多条混合 → abort（遇到第一个 substantive 就 abort）
    - 都是寒暄 → continue
    - 灰区 + LLM 判 substantive → abort
    - 灰区 + LLM 判 non-substantive → continue
    - fetch 失败 → abort（阻断过期公开回复）
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from zd_agent.runtime.pre_send_check import (
    is_substantive_heuristic,
    is_substantive_llm,
    pre_send_check_or_abort,
)


# ============================================================================
# is_substantive_heuristic — 各种硬规则
# ============================================================================

class TestHeuristicTrueRules:
    """硬规则 → True（实质性）"""

    def test_has_image_attachment(self) -> None:
        msg = {"body": "hi", "image_urls": ["https://x/img.png"]}
        assert is_substantive_heuristic(msg) is True

    def test_image_outranks_short_text(self) -> None:
        """图片就算文字短也算实质"""
        msg = {"body": "ok", "image_urls": ["https://x/img.png"]}
        assert is_substantive_heuristic(msg) is True

    def test_contains_uuid(self) -> None:
        msg = {"body": "workspace is 1708272e-97d1-413c-9c8a-7a30d7022931"}
        assert is_substantive_heuristic(msg) is True

    def test_contains_uppercase_uuid(self) -> None:
        msg = {"body": "App ID 6A03DF58-7B41-0DEF-3D5B-FC6D6A03DF58"}
        assert is_substantive_heuristic(msg) is True

    def test_contains_url(self) -> None:
        msg = {"body": "see https://app.dify.ai/x"}
        assert is_substantive_heuristic(msg) is True

    def test_contains_http_url(self) -> None:
        msg = {"body": "http://example.com"}
        assert is_substantive_heuristic(msg) is True

    def test_contains_code_block(self) -> None:
        msg = {"body": "got error\n```\nTypeError: foo\n```"}
        assert is_substantive_heuristic(msg) is True

    def test_contains_error_trace(self) -> None:
        msg = {"body": "I see Traceback in console"}
        assert is_substantive_heuristic(msg) is True

    def test_contains_500_status(self) -> None:
        msg = {"body": "API returns 500 Internal Server Error"}
        assert is_substantive_heuristic(msg) is True

    def test_long_text_over_100_chars(self) -> None:
        msg = {"body": "x" * 101}
        assert is_substantive_heuristic(msg) is True


class TestHeuristicFalseRules:
    """IGNORE_LIST → False（非实质）"""

    @pytest.mark.parametrize("body", [
        "谢谢", "thanks", "Thanks", "thx", "OK", "ok",
        "got it", "Got it", "嗯", "好的", "在吗", "急", "any update?",
        "👍", "🙏",
    ])
    def test_ignore_list_returns_false(self, body: str) -> None:
        msg = {"body": body}
        assert is_substantive_heuristic(msg) is False

    @pytest.mark.parametrize("body", [
        "谢谢！", "Thanks.", "OK!", "在吗？",
    ])
    def test_ignore_list_with_punctuation(self, body: str) -> None:
        """带标点 / 大小写变体也算 IGNORE"""
        assert is_substantive_heuristic({"body": body}) is False

    def test_empty_body_no_image(self) -> None:
        assert is_substantive_heuristic({"body": "", "image_urls": []}) is False

    def test_only_whitespace(self) -> None:
        assert is_substantive_heuristic({"body": "   \n  "}) is False


class TestHeuristicGreyZone:
    """灰区 → None（让 LLM 判断）"""

    @pytest.mark.parametrize("body", [
        "didn't work",
        "still broken",
        "也一样",
        "请问",
        "我试试",
        "what should I do next?",
        "doesn't help",
    ])
    def test_short_unstructured_text_returns_none(self, body: str) -> None:
        assert is_substantive_heuristic({"body": body}) is None


# ============================================================================
# is_substantive_llm — mocked HTTP
# ============================================================================

class TestLLMJudge:
    @pytest.mark.asyncio
    async def test_substantive_true_response(self) -> None:
        mock_resp = AsyncMock()
        mock_resp.status_code = 200
        mock_resp.json = lambda: {
            "choices": [{"message": {"content":
                '{"substantive": true, "reason": "客户给了具体行为"}'
            }}]
        }
        with patch("httpx.AsyncClient.post", AsyncMock(return_value=mock_resp)):
            result = await is_substantive_llm(
                "didn't help, still same error", model="x", api_key="k",
            )
        assert result is True

    @pytest.mark.asyncio
    async def test_reasoning_effort_is_sent_to_llm(self) -> None:
        mock_resp = AsyncMock()
        mock_resp.status_code = 200
        mock_resp.json = lambda: {
            "choices": [{"message": {"content":
                '{"substantive": false, "reason": "pure acknowledgement"}'
            }}]
        }
        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        client.post = AsyncMock(return_value=mock_resp)
        with patch(
            "zd_agent.runtime.pre_send_check.httpx.AsyncClient",
            return_value=client,
        ) as client_cls:
            await is_substantive_llm(
                "still there?",
                model="gpt-5.6-terra",
                reasoning_effort="high",
                api_key="k",
            )

        assert client.post.await_args.kwargs["json"]["reasoning_effort"] == "high"
        assert client.post.await_args.kwargs["json"]["max_completion_tokens"] == 8192
        assert client_cls.call_args.kwargs["timeout"] == 120.0

    @pytest.mark.asyncio
    async def test_substantive_false_response(self) -> None:
        mock_resp = AsyncMock()
        mock_resp.status_code = 200
        mock_resp.json = lambda: {
            "choices": [{"message": {"content":
                '{"substantive": false, "reason": "纯催促"}'
            }}]
        }
        with patch("httpx.AsyncClient.post", AsyncMock(return_value=mock_resp)):
            result = await is_substantive_llm(
                "什么时候能修好啊", model="x", api_key="k",
            )
        assert result is False

    @pytest.mark.asyncio
    async def test_http_error_conservative_true(self) -> None:
        """HTTP 异常 → 保守返回 True（让 caller abort，宁可重新分析）"""
        with patch(
            "httpx.AsyncClient.post",
            AsyncMock(side_effect=RuntimeError("connection error")),
        ):
            result = await is_substantive_llm("x", model="m", api_key="k")
        assert result is True

    @pytest.mark.asyncio
    async def test_non_200_conservative_true(self) -> None:
        mock_resp = AsyncMock()
        mock_resp.status_code = 500
        mock_resp.text = "internal server error"
        with patch("httpx.AsyncClient.post", AsyncMock(return_value=mock_resp)):
            result = await is_substantive_llm("x", model="m", api_key="k")
        assert result is True

    @pytest.mark.asyncio
    async def test_malformed_json_conservative_true(self) -> None:
        mock_resp = AsyncMock()
        mock_resp.status_code = 200
        mock_resp.json = lambda: {"choices": [{"message": {"content":
            "not json at all"
        }}]}
        with patch("httpx.AsyncClient.post", AsyncMock(return_value=mock_resp)):
            result = await is_substantive_llm("x", model="m", api_key="k")
        assert result is True


# ============================================================================
# pre_send_check_or_abort — 完整流程
# ============================================================================

def _make_fetcher(msgs: list[dict]):
    """返回 fetch_messages_fn 的 async 实现"""
    async def _fetch():
        return msgs
    return _fetch


class TestPreSendCheckOrAbort:
    @pytest.mark.asyncio
    async def test_no_new_messages_continues(self) -> None:
        msgs = [{"id": "m1", "body": "first message"}]
        result = await pre_send_check_or_abort(
            ticket_id=1,
            snapshot_count=1,
            snapshot_last_id="m1",
            fetch_messages_fn=_make_fetcher(msgs),
            skip_llm=True,
        )
        assert result.continue_send is True
        assert result.new_messages_count == 0

    @pytest.mark.asyncio
    async def test_uses_snapshot_last_id_when_count_is_unchanged(self) -> None:
        """消息列表过滤导致 count 未变时，仍应按 last_id 找到后续新消息。"""
        msgs = [
            {"id": "m0", "body": "older message"},
            {
                "id": "m2",
                "body": "Workspace 1708272e-97d1-413c-9c8a-7a30d7022931",
            },
        ]
        result = await pre_send_check_or_abort(
            ticket_id=1,
            snapshot_count=2,
            snapshot_last_id="m0",
            fetch_messages_fn=_make_fetcher(msgs),
            skip_llm=True,
        )

        assert result.continue_send is False
        assert result.new_messages_count == 1

    @pytest.mark.asyncio
    async def test_missing_snapshot_last_id_aborts_conservatively(self) -> None:
        """找不到 snapshot last_id 时，避免误发过期回复。"""
        msgs = [
            {"id": "m2", "body": "thanks"},
        ]
        result = await pre_send_check_or_abort(
            ticket_id=1,
            snapshot_count=1,
            snapshot_last_id="missing",
            fetch_messages_fn=_make_fetcher(msgs),
            skip_llm=True,
        )

        assert result.continue_send is False
        assert result.reason == "snapshot_last_id_missing"
        assert result.new_messages_count == 1

    @pytest.mark.asyncio
    async def test_new_image_attachment_aborts(self) -> None:
        msgs = [
            {"id": "m1", "body": "original"},
            {"id": "m2", "body": "see screenshot", "image_urls": ["x"]},
        ]
        result = await pre_send_check_or_abort(
            ticket_id=1,
            snapshot_count=1,
            snapshot_last_id="m1",
            fetch_messages_fn=_make_fetcher(msgs),
            skip_llm=True,
        )
        assert result.continue_send is False
        assert "substantive_heuristic" in result.reason
        assert result.new_messages_count == 1

    @pytest.mark.asyncio
    async def test_new_thanks_message_continues(self) -> None:
        msgs = [
            {"id": "m1", "body": "original problem"},
            {"id": "m2", "body": "thanks!"},
        ]
        result = await pre_send_check_or_abort(
            ticket_id=1,
            snapshot_count=1,
            snapshot_last_id="m1",
            fetch_messages_fn=_make_fetcher(msgs),
            skip_llm=True,
        )
        assert result.continue_send is True
        assert result.new_messages_count == 1
        assert result.recovery is not None
        assert result.recovery.next_snapshot_count == 2
        assert result.recovery.next_snapshot_last_id == "m2"

    @pytest.mark.asyncio
    async def test_multiple_new_msgs_abort_on_first_substantive(self) -> None:
        """3 条新消息：thanks（非实质）+ 截图（实质）+ thanks → 应该 abort"""
        msgs = [
            {"id": "m1", "body": "original"},
            {"id": "m2", "body": "thanks"},
            {"id": "m3", "body": "but", "image_urls": ["img"]},
            {"id": "m4", "body": "ok"},
        ]
        result = await pre_send_check_or_abort(
            ticket_id=1,
            snapshot_count=1,
            snapshot_last_id="m1",
            fetch_messages_fn=_make_fetcher(msgs),
            skip_llm=True,
        )
        assert result.continue_send is False
        assert result.new_messages_count == 3

    @pytest.mark.asyncio
    async def test_grey_zone_skip_llm_aborts_conservatively(self) -> None:
        """skip_llm=True 时灰区按 abort 保守处理"""
        msgs = [
            {"id": "m1", "body": "original"},
            {"id": "m2", "body": "didn't work"},  # 灰区
        ]
        result = await pre_send_check_or_abort(
            ticket_id=1,
            snapshot_count=1,
            snapshot_last_id="m1",
            fetch_messages_fn=_make_fetcher(msgs),
            skip_llm=True,
        )
        assert result.continue_send is False
        assert result.reason == "substantive_llm_skipped"

    @pytest.mark.asyncio
    async def test_grey_zone_llm_says_substantive_aborts(self) -> None:
        msgs = [
            {"id": "m1", "body": "original"},
            {"id": "m2", "body": "still broken"},
        ]
        with patch(
            "zd_agent.runtime.pre_send_check.is_substantive_llm",
            AsyncMock(return_value=True),
        ):
            result = await pre_send_check_or_abort(
                ticket_id=1,
                snapshot_count=1,
                snapshot_last_id="m1",
                fetch_messages_fn=_make_fetcher(msgs),
                model="x", api_key="k",
            )
        assert result.continue_send is False

    @pytest.mark.asyncio
    async def test_grey_zone_llm_says_not_substantive_continues(self) -> None:
        msgs = [
            {"id": "m1", "body": "original"},
            {"id": "m2", "body": "what time?"},  # 灰区，LLM 判 non-substantive
        ]
        with patch(
            "zd_agent.runtime.pre_send_check.is_substantive_llm",
            AsyncMock(return_value=False),
        ):
            result = await pre_send_check_or_abort(
                ticket_id=1,
                snapshot_count=1,
                snapshot_last_id="m1",
                fetch_messages_fn=_make_fetcher(msgs),
                model="x", api_key="k",
            )
        assert result.continue_send is True

    @pytest.mark.asyncio
    async def test_fetch_error_blocks_public_reply(self) -> None:
        async def _bad_fetch():
            raise RuntimeError("zendesk API timeout")

        result = await pre_send_check_or_abort(
            ticket_id=1,
            snapshot_count=1,
            snapshot_last_id="m1",
            fetch_messages_fn=_bad_fetch,
        )
        assert result.continue_send is False
        assert "fetch_error" in result.reason
