import unittest

from zd_agent.tools.zendesk import (
    _detect_reply_language,
    _extract_reply_language_hint,
)


class ZendeskLanguageTest(unittest.TestCase):
    def test_detect_reply_language_prefers_japanese_when_kana_present(self) -> None:
        self.assertEqual(_detect_reply_language("問題が発生しています。ご確認ください。"), "ja")

    def test_extract_reply_language_hint_from_latest_public_customer_reply(self) -> None:
        thread_type = "comment_list"
        thread_items = [
            {"public": True, "body": "First customer message in English"},
            {"public": False, "body": "Internal note from support"},
            {"public": True, "body": "日本語で失礼します。問題が発生しています。"},
        ]

        hint = _extract_reply_language_hint(
            thread_type=thread_type,
            thread_items=thread_items,
            ticket_description="Initial English description",
        )

        self.assertIsNotNone(hint)
        assert hint is not None
        self.assertEqual(hint["language"], "ja")
        self.assertIn("日本語", hint["evidence"])

    def test_extract_reply_language_hint_falls_back_to_ticket_description(self) -> None:
        hint = _extract_reply_language_hint(
            thread_type="comment_list",
            thread_items=[{"public": False, "body": "Internal note only"}],
            ticket_description="Please help, login is broken",
        )

        self.assertIsNotNone(hint)
        assert hint is not None
        self.assertEqual(hint["language"], "en")
        self.assertIn("Please help", hint["evidence"])


if __name__ == "__main__":
    unittest.main()
