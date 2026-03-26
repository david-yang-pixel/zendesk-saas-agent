import unittest
from types import SimpleNamespace

from zd_agent.runtime.archive_meta import build_archive_meta
from zd_agent.session.agent_session import AgentSession, SessionStatus


class ArchiveMetaTest(unittest.TestCase):
    def test_build_archive_meta_includes_failed_tools_and_error(self) -> None:
        session = AgentSession(ticket_id=1810)
        session.status = SessionStatus.FAILED
        session.round_count = 7
        session.reply_sent = False
        session.token_usage.prompt_tokens = 100
        session.token_usage.completion_tokens = 20
        session.error_message = "sandbox_search 超时"
        session.log_tool_call(
            "zendesk_get_ticket",
            {"ticket_id": 1810},
            "ok",
            success=True,
            error=None,
        )
        session.log_tool_call(
            "sandbox_search",
            {"pattern": "oauth"},
            "❌ 错误: timeout",
            success=False,
            error="timeout",
        )

        meta = build_archive_meta(
            session=session,
            archived_at="2026-03-26T10:00:00+00:00",
            elapsed_seconds=12.3,
        )

        self.assertEqual(meta["status"], "failed")
        self.assertEqual(meta["error"], "sandbox_search 超时")
        self.assertEqual(meta["failed_tool_count"], 1)
        self.assertEqual(len(meta["failed_tools"]), 1)
        self.assertEqual(meta["failed_tools"][0]["tool"], "sandbox_search")
        self.assertEqual(meta["failed_tools"][0]["error"], "timeout")
        self.assertFalse(meta["tool_calls"][1]["success"])

    def test_build_archive_meta_omits_error_when_session_succeeds(self) -> None:
        session = AgentSession(ticket_id=1789)
        session.status = SessionStatus.COMPLETED
        session.round_count = 3
        session.token_usage.prompt_tokens = 50
        session.token_usage.completion_tokens = 10
        session.log_tool_call(
            "official_docs_search",
            {"query": "sso"},
            "ok",
            success=True,
            error=None,
        )

        meta = build_archive_meta(
            session=session,
            archived_at="2026-03-26T10:10:00+00:00",
            elapsed_seconds=3.2,
        )

        self.assertEqual(meta["status"], "completed")
        self.assertIsNone(meta["error"])
        self.assertEqual(meta["failed_tool_count"], 0)
        self.assertEqual(meta["failed_tools"], [])
        self.assertTrue(meta["tool_calls"][0]["success"])


if __name__ == "__main__":
    unittest.main()
