import unittest
from unittest.mock import patch

from zd_agent.mention.detector import is_mention


class TestIsMention(unittest.TestCase):
    def test_detects_mention_in_internal_note(self) -> None:
        comment = {"is_public": False, "body": "Hey @support-agent can you look at this?"}
        self.assertTrue(is_mention(comment, "@support-agent"))

    def test_ignores_public_comment(self) -> None:
        comment = {"is_public": True, "body": "Hey @support-agent can you look at this?"}
        self.assertFalse(is_mention(comment, "@support-agent"))

    def test_case_insensitive(self) -> None:
        comment = {"is_public": False, "body": "Hey @Support-Agent please help"}
        self.assertTrue(is_mention(comment, "@support-agent"))

    def test_no_mention(self) -> None:
        comment = {"is_public": False, "body": "This is a regular internal note"}
        self.assertFalse(is_mention(comment, "@support-agent"))

    def test_missing_is_public_treated_as_public(self) -> None:
        comment = {"body": "Hey @support-agent"}
        self.assertFalse(is_mention(comment, "@support-agent"))

    def test_empty_body(self) -> None:
        comment = {"is_public": False, "body": ""}
        self.assertFalse(is_mention(comment, "@support-agent"))

    def test_detects_zendesk_markdown_mention(self) -> None:
        # Zendesk API 将 @mention 转为 **username** 格式
        comment = {"is_public": False, "body": "**support-agent** 1"}
        self.assertTrue(is_mention(comment, "@support-agent"))

    def test_detects_zendesk_markdown_mention_case_insensitive(self) -> None:
        comment = {"is_public": False, "body": "**Support-Agent** please help"}
        self.assertTrue(is_mention(comment, "@support-agent"))

    # ---- Self-trigger guard tests (人 + agent 共用同一账号场景) ----

    def test_human_writing_mention_in_ui_triggers_even_if_same_account_id(self) -> None:
        """人在 Zendesk UI 写 @yonggang 内部备注，via.channel = web，应触发。
        即使 author_id 和 agent 自己用的 self_id 相同。"""
        comment = {
            "is_public": False,
            "body": "@yonggang 看下这个问题",
            "author_id": 12345,
            "via": {"channel": "web"},
        }
        with patch(
            "zd_agent.mention.detector.get_self_user_id", return_value=12345,
        ):
            self.assertTrue(is_mention(comment, "@yonggang"))

    def test_agent_api_post_with_same_account_id_does_not_trigger(self) -> None:
        """agent 通过 API 写 @yonggang，via.channel = api 且 author_id 匹配 → 跳过，
        防止自触发循环。"""
        comment = {
            "is_public": False,
            "body": "## 内部分析\n@yonggang follow-up",
            "author_id": 12345,
            "via": {"channel": "api"},
        }
        with patch(
            "zd_agent.mention.detector.get_self_user_id", return_value=12345,
        ):
            self.assertFalse(is_mention(comment, "@yonggang"))

    def test_other_bot_via_api_with_different_author_still_triggers(self) -> None:
        """其他 bot 通过 API 写 @yonggang，author_id 不同 → 应触发（不是自触发）。"""
        comment = {
            "is_public": False,
            "body": "@yonggang heads up",
            "author_id": 99999,  # 别的账号
            "via": {"channel": "api"},
        }
        with patch(
            "zd_agent.mention.detector.get_self_user_id", return_value=12345,
        ):
            self.assertTrue(is_mention(comment, "@yonggang"))

    def test_no_via_field_with_matching_author_skips_for_safety(self) -> None:
        """没有 via 字段（不知道是不是人类操作）+ author_id 匹配 self_id → 保守跳过，
        避免历史评论 / 老格式数据导致自触发循环。"""
        comment = {
            "is_public": False,
            "body": "@yonggang",
            "author_id": 12345,
        }
        with patch(
            "zd_agent.mention.detector.get_self_user_id", return_value=12345,
        ):
            self.assertFalse(is_mention(comment, "@yonggang"))

    def test_real_zendesk_shape_uses_public_not_is_public(self) -> None:
        """真实 Zendesk Comments API 返回的字段是 ``public`` 不是 ``is_public``。
        之前的 detector 用 ``is_public`` → 永远拿 default True → 所有 mention 被
        过滤为 public 而漏触发。这是导致 #2320 工单 @yonggang 不响应的根因。"""
        comment = {
            "public": False,  # ← Zendesk 真实字段名
            "body": "**yonggang** 看下这个问题",
            "author_id": 45433304089364,
            "via": {"channel": "web"},
        }
        with patch(
            "zd_agent.mention.detector.get_self_user_id",
            return_value=45433304089364,
        ):
            self.assertTrue(is_mention(comment, "@yonggang"))

    def test_real_zendesk_shape_public_true_returns_false(self) -> None:
        """``public: true`` 表示公开评论，不应触发。"""
        comment = {
            "public": True,
            "body": "@yonggang please help",
            "via": {"channel": "web"},
        }
        self.assertFalse(is_mention(comment, "@yonggang"))

    def test_human_via_chat_channel_triggers(self) -> None:
        """messaging / chat 渠道也算人类操作。"""
        comment = {
            "is_public": False,
            "body": "@yonggang please check",
            "author_id": 12345,
            "via": {"channel": "messaging"},
        }
        with patch(
            "zd_agent.mention.detector.get_self_user_id", return_value=12345,
        ):
            self.assertTrue(is_mention(comment, "@yonggang"))


if __name__ == "__main__":
    unittest.main()
