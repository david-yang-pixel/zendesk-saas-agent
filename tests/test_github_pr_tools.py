import unittest
from unittest.mock import AsyncMock, patch

from zd_agent.session.agent_session import AgentSession
from zd_agent.tools.github_pr import GithubCodeSearch, GithubGetPr, GithubSearchPrs


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


class TestGithubCodeSearch(unittest.IsolatedAsyncioTestCase):
    @patch("zd_agent.tools.github_pr._github_request", new_callable=AsyncMock)
    async def test_code_search_org_scope(self, mock_request: AsyncMock) -> None:
        """默认 scope=langgenius → query 拼成 'org:langgenius'。"""
        mock_request.return_value = {
            "total_count": 7,
            "items": [
                {
                    "repository": {"full_name": "langgenius/dify-plugins"},
                    "path": "tools/lark_bitable/auth.py",
                    "html_url": "https://github.com/langgenius/dify-plugins/blob/main/tools/lark_bitable/auth.py",
                    "score": 12.3,
                    "text_matches": [
                        {"fragment": "raise InvalidCredential('missing scope')"},
                    ],
                }
            ],
        }
        tool = GithubCodeSearch()
        result = await tool.execute(
            {"query": "InvalidCredential lark"},
            AgentSession(ticket_id=1),
        )
        self.assertTrue(result.success)
        # 验证 q 参数被正确拼装
        called_params = mock_request.call_args.kwargs.get("params", {})
        self.assertIn("org:langgenius", called_params["q"])
        self.assertIn("InvalidCredential", called_params["q"])
        # 输出包含仓库 + 文件 + fragment
        self.assertIn("langgenius/dify-plugins", result.output)
        self.assertIn("tools/lark_bitable/auth.py", result.output)
        self.assertIn("missing scope", result.output)

    @patch("zd_agent.tools.github_pr._github_request", new_callable=AsyncMock)
    async def test_code_search_repo_scope(self, mock_request: AsyncMock) -> None:
        """scope='owner/repo' → query 拼成 'repo:owner/repo'。"""
        mock_request.return_value = {"total_count": 0, "items": []}
        tool = GithubCodeSearch()
        await tool.execute(
            {"query": "foo", "scope": "langgenius/dify-plugins"},
            AgentSession(ticket_id=1),
        )
        params = mock_request.call_args.kwargs.get("params", {})
        self.assertIn("repo:langgenius/dify-plugins", params["q"])
        self.assertNotIn("org:", params["q"])

    @patch("zd_agent.tools.github_pr._github_request", new_callable=AsyncMock)
    async def test_code_search_with_language_and_path(self, mock_request: AsyncMock) -> None:
        mock_request.return_value = {"total_count": 0, "items": []}
        tool = GithubCodeSearch()
        await tool.execute(
            {
                "query": "foo",
                "language": "python",
                "path": "core/tools/lark",
                "limit": 5,
            },
            AgentSession(ticket_id=1),
        )
        params = mock_request.call_args.kwargs.get("params", {})
        self.assertIn("language:python", params["q"])
        self.assertIn("path:core/tools/lark", params["q"])
        self.assertEqual(params["per_page"], 5)

    @patch("zd_agent.tools.github_pr._github_request", new_callable=AsyncMock)
    async def test_code_search_no_results(self, mock_request: AsyncMock) -> None:
        mock_request.return_value = {"total_count": 0, "items": []}
        tool = GithubCodeSearch()
        result = await tool.execute(
            {"query": "this-thing-does-not-exist"},
            AgentSession(ticket_id=1),
        )
        self.assertTrue(result.success)
        self.assertIn("未找到", result.output)

    async def test_code_search_empty_query_rejected(self) -> None:
        tool = GithubCodeSearch()
        result = await tool.execute({"query": "  "}, AgentSession(ticket_id=1))
        self.assertFalse(result.success)
        self.assertIn("不能为空", result.error)
