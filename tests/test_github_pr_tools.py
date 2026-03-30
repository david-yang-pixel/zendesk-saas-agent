import unittest
from unittest.mock import AsyncMock, patch

from zd_agent.session.agent_session import AgentSession
from zd_agent.tools.github_pr import GithubGetPr, GithubSearchPrs


class TestGithubSearchPrs(unittest.IsolatedAsyncioTestCase):
    @patch("zd_agent.tools.github_pr._github_request", new_callable=AsyncMock)
    async def test_search_prs_formats_results(self, mock_request: AsyncMock) -> None:
        mock_request.return_value = {
            "items": [
                {
                    "number": 123,
                    "title": "fix: handle null workspace",
                    "state": "open",
                    "html_url": "https://github.com/example/repo/pull/123",
                    "user": {"login": "alice"},
                }
            ]
        }
        tool = GithubSearchPrs()
        result = await tool.execute(
            {"repo": "langgenius/dify", "query": "workspace null", "limit": 5},
            AgentSession(ticket_id=1),
        )
        self.assertTrue(result.success)
        self.assertIn("#123", result.output)
        self.assertIn("open", result.output)


class TestGithubGetPr(unittest.IsolatedAsyncioTestCase):
    @patch("zd_agent.tools.github_pr._github_request", new_callable=AsyncMock)
    async def test_get_pr_formats_summary(self, mock_request: AsyncMock) -> None:
        mock_request.return_value = {
            "number": 456,
            "title": "fix: retry auth flow",
            "state": "closed",
            "merged_at": "2026-03-20T12:00:00Z",
            "html_url": "https://github.com/example/repo/pull/456",
            "body": "Fixes auth retry issue",
            "user": {"login": "bob"},
        }
        tool = GithubGetPr()
        result = await tool.execute(
            {"repo": "langgenius/dify", "pr_number": 456},
            AgentSession(ticket_id=1),
        )
        self.assertTrue(result.success)
        self.assertIn("merged", result.output.lower())
        self.assertIn("auth retry", result.output.lower())
