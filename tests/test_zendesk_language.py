import unittest

from zd_agent.tools.zendesk import (
    _detect_reply_language,
    _extract_reply_language_hint,
    _strip_technical_content,
)


class StripTechnicalContentTest(unittest.TestCase):
    """确保技术内容被正确剔除，自然语言保留。"""

    def test_strips_fenced_code_blocks(self) -> None:
        text = "你好\n```\nError: something broke\nat Object.main\n```\n请帮忙看看"
        result = _strip_technical_content(text)
        self.assertNotIn("Error", result)
        self.assertIn("你好", result)
        self.assertIn("请帮忙看看", result)

    def test_strips_inline_code(self) -> None:
        text = "执行 `kubectl get pods` 之后报错了"
        result = _strip_technical_content(text)
        self.assertNotIn("kubectl", result)
        self.assertIn("执行", result)
        self.assertIn("之后报错了", result)

    def test_strips_urls(self) -> None:
        text = "访问 https://console.dify.ai/apps 报500了"
        result = _strip_technical_content(text)
        self.assertNotIn("https://", result)
        self.assertIn("访问", result)

    def test_strips_log_lines(self) -> None:
        text = "部署后看到这些日志：\n2024-01-15T10:30:00 ERROR something failed\nINFO [main] starting up\n请问正常吗"
        result = _strip_technical_content(text)
        self.assertNotIn("ERROR something", result)
        self.assertNotIn("INFO [main]", result)
        self.assertIn("部署后看到这些日志", result)
        self.assertIn("请问正常吗", result)

    def test_strips_stack_traces(self) -> None:
        text = '报错了\nTraceback (most recent call last):\n  File "/app/main.py", line 42\nTypeError: cannot unpack\n怎么办'
        result = _strip_technical_content(text)
        self.assertNotIn("Traceback", result)
        self.assertNotIn('File "/app', result)
        self.assertNotIn("TypeError", result)
        self.assertIn("报错了", result)
        self.assertIn("怎么办", result)


class DetectReplyLanguageTest(unittest.TestCase):
    def test_prefers_japanese_when_kana_present(self) -> None:
        self.assertEqual(
            _detect_reply_language("問題が発生しています。ご確認ください。"), "ja"
        )

    def test_chinese_with_english_error_logs(self) -> None:
        """中文客户贴了大量英文报错——应判定为中文"""
        text = (
            "你好，我部署的时候遇到报错：\n"
            "```\n"
            "Error: ImagePullBackOff\n"
            '  Warning  Failed  3m  kubelet  Failed to pull image "dify/api:1.2.3"\n'
            "  Warning  Failed  3m  kubelet  Error response from daemon\n"
            '  Normal   Pulling  2m (x4 over 3m)  kubelet  Pulling image "dify/api:1.2.3"\n'
            "```\n"
            "请问怎么解决？"
        )
        self.assertEqual(_detect_reply_language(text), "zh")

    def test_chinese_with_inline_english_errors(self) -> None:
        """中文客户消息中混杂无代码块的英文错误"""
        text = (
            "你好，升级到1.2.3之后出现问题\n"
            "TypeError: Cannot read property 'undefined' of null\n"
            "at Object.handleRequest (/app/server.js:42:15)\n"
            "at Router.dispatch (/app/node_modules/express/lib/router/index.js:24:12)\n"
            "请问这是什么原因？"
        )
        self.assertEqual(_detect_reply_language(text), "zh")

    def test_japanese_with_english_errors(self) -> None:
        """日本語のお客様が英語のエラーを貼った場合"""
        text = (
            "こんにちは、以下のエラーが発生しています：\n"
            "```\n"
            "ConnectionError: Failed to connect to database\n"
            "RuntimeError: Worker process exited unexpectedly\n"
            "```\n"
            "確認をお願いします。"
        )
        self.assertEqual(_detect_reply_language(text), "ja")

    def test_pure_english(self) -> None:
        self.assertEqual(
            _detect_reply_language("Hello, I need help with my deployment"), "en"
        )

    def test_pure_chinese(self) -> None:
        self.assertEqual(_detect_reply_language("你好，我需要帮助"), "zh")

    def test_empty_text(self) -> None:
        self.assertEqual(_detect_reply_language(""), "")
        self.assertEqual(_detect_reply_language("   "), "")

    def test_only_code_falls_back_to_original(self) -> None:
        """客户只贴了代码（无自然语言），回退到原文检测"""
        text = "```\nError: something went wrong\n```"
        self.assertEqual(_detect_reply_language(text), "en")


class ExtractReplyLanguageHintTest(unittest.TestCase):
    def test_from_latest_public_customer_reply(self) -> None:
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

    def test_falls_back_to_ticket_description(self) -> None:
        hint = _extract_reply_language_hint(
            thread_type="comment_list",
            thread_items=[{"public": False, "body": "Internal note only"}],
            ticket_description="Please help, login is broken",
        )

        self.assertIsNotNone(hint)
        assert hint is not None
        self.assertEqual(hint["language"], "en")
        self.assertIn("Please help", hint["evidence"])

    def test_chinese_customer_with_english_error_in_comment(self) -> None:
        """工单评论里中文客户贴了英文报错"""
        thread_items = [
            {
                "public": True,
                "body": (
                    "你好，升级后遇到以下报错：\n"
                    "Error: UPGRADE FAILED: release dify not found\n"
                    "helm upgrade --install dify dify/dify --version 1.2.3\n"
                    "请问如何处理？"
                ),
            },
        ]

        hint = _extract_reply_language_hint(
            thread_type="comment_list",
            thread_items=thread_items,
            ticket_description="Helm upgrade failed",
        )

        self.assertIsNotNone(hint)
        assert hint is not None
        self.assertEqual(hint["language"], "zh")


if __name__ == "__main__":
    unittest.main()
