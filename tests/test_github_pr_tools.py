import unittest
from unittest.mock import AsyncMock, patch

from zd_agent.session.agent_session import AgentSession
from zd_agent.tools.github_pr import GithubCodeSearch, GithubGetPr, GithubSearchPrs


class TestGithubSearchPrs(unittest.IsolatedAsyncioTestCase):
    @patch("zd_agent.tools.github_pr.call_github_tool", new_callable=AsyncMock)
    async def test_search_prs_formats_results(self, mock_call: AsyncMock) -> None:
        mock_call.return_value = {
            "success": True,
            "data": {
                "items": [
                    {
                        "number": 123,
                        "title": "fix: handle null workspace",
                        "state": "open",
                        "html_url": "https://github.com/example/repo/pull/123",
                        "user": {"login": "alice"},
                    }
                ]
            },
        }
        result = await GithubSearchPrs().execute(
            {"repo": "langgenius/dify", "query": "workspace null", "limit": 5},
            AgentSession(ticket_id=1),
        )

        self.assertTrue(result.success)
        self.assertIn("#123", result.output)
        self.assertIn("open", result.output)
        mock_call.assert_awaited_once()
        self.assertEqual(mock_call.await_args.args[0], "github_search_prs")

    @patch("zd_agent.tools.github_pr.call_github_tool", new_callable=AsyncMock)
    async def test_search_prs_surfaces_mcp_failure(self, mock_call: AsyncMock) -> None:
        mock_call.return_value = {
            "success": False,
            "code": "GITHUB_MCP_URL_MISSING",
            "error": "not configured",
        }

        result = await GithubSearchPrs().execute(
            {"repo": "langgenius/dify", "query": "workspace null"},
            AgentSession(ticket_id=1),
        )

        self.assertFalse(result.success)
        self.assertIn("GITHUB_MCP_URL_MISSING", result.error or "")

    async def test_search_prs_rejects_invalid_limit(self) -> None:
        result = await GithubSearchPrs().execute(
            {"repo": "langgenius/dify", "query": "workspace null", "limit": "many"},
            AgentSession(ticket_id=1),
        )
        self.assertFalse(result.success)
        self.assertIn("limit", result.error or "")

    async def test_search_prs_rejects_invalid_string_args(self) -> None:
        tool = GithubSearchPrs()
        bad_repo = await tool.execute(
            {"repo": ["langgenius/dify"], "query": "workspace null"},
            AgentSession(ticket_id=1),
        )
        bad_query = await tool.execute(
            {"repo": "langgenius/dify", "query": {"text": "workspace null"}},
            AgentSession(ticket_id=1),
        )
        self.assertEqual(bad_repo.error, "repo must be a string")
        self.assertEqual(bad_query.error, "query must be a string")


class TestGithubGetPr(unittest.IsolatedAsyncioTestCase):
    @patch("zd_agent.tools.github_pr.call_github_tool", new_callable=AsyncMock)
    async def test_get_pr_formats_summary(self, mock_call: AsyncMock) -> None:
        mock_call.return_value = {
            "success": True,
            "data": {
                "number": 456,
                "title": "fix: retry auth flow",
                "state": "closed",
                "merged_at": "2026-03-20T12:00:00Z",
                "html_url": "https://github.com/example/repo/pull/456",
                "body": "Fixes auth retry issue",
                "user": {"login": "bob"},
            },
        }
        result = await GithubGetPr().execute(
            {"repo": "langgenius/dify", "pr_number": 456},
            AgentSession(ticket_id=1),
        )
        self.assertTrue(result.success)
        self.assertIn("merged", result.output.lower())
        self.assertIn("auth retry", result.output.lower())

    async def test_get_pr_rejects_invalid_pr_number(self) -> None:
        result = await GithubGetPr().execute(
            {"repo": "langgenius/dify", "pr_number": "abc"},
            AgentSession(ticket_id=1),
        )
        self.assertFalse(result.success)
        self.assertIn("pr_number", result.error or "")

    async def test_get_pr_rejects_invalid_repo_arg(self) -> None:
        result = await GithubGetPr().execute(
            {"repo": ["langgenius/dify"], "pr_number": 123},
            AgentSession(ticket_id=1),
        )
        self.assertEqual(result.error, "repo must be a string")


class TestGithubCodeSearch(unittest.IsolatedAsyncioTestCase):
    @patch("zd_agent.tools.github_pr.call_github_tool", new_callable=AsyncMock)
    async def test_code_search_org_scope(self, mock_call: AsyncMock) -> None:
        mock_call.return_value = {
            "success": True,
            "data": {
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
            },
        }
        result = await GithubCodeSearch().execute(
            {"query": "InvalidCredential lark"},
            AgentSession(ticket_id=1),
        )

        self.assertTrue(result.success)
        arguments = mock_call.await_args.args[1]
        self.assertIn("org:langgenius", arguments["full_query"])
        self.assertIn("InvalidCredential", arguments["full_query"])
        self.assertIn("langgenius/dify-plugins", result.output)
        self.assertIn("missing scope", result.output)

    @patch("zd_agent.tools.github_pr.call_github_tool", new_callable=AsyncMock)
    async def test_code_search_repo_scope(self, mock_call: AsyncMock) -> None:
        mock_call.return_value = {"success": True, "data": {"total_count": 0, "items": []}}
        await GithubCodeSearch().execute(
            {"query": "foo", "scope": "langgenius/dify-plugins"},
            AgentSession(ticket_id=1),
        )
        full_query = mock_call.await_args.args[1]["full_query"]
        self.assertIn("repo:langgenius/dify-plugins", full_query)
        self.assertNotIn("org:", full_query)

    @patch("zd_agent.tools.github_pr.call_github_tool", new_callable=AsyncMock)
    async def test_code_search_with_language_and_path(self, mock_call: AsyncMock) -> None:
        mock_call.return_value = {"success": True, "data": {"total_count": 0, "items": []}}
        await GithubCodeSearch().execute(
            {
                "query": "foo",
                "language": "python",
                "path": "core/tools/lark",
                "limit": 5,
            },
            AgentSession(ticket_id=1),
        )
        arguments = mock_call.await_args.args[1]
        self.assertIn("language:python", arguments["full_query"])
        self.assertIn("path:core/tools/lark", arguments["full_query"])
        self.assertEqual(arguments["limit"], 5)

    @patch("zd_agent.tools.github_pr.call_github_tool", new_callable=AsyncMock)
    async def test_code_search_no_results(self, mock_call: AsyncMock) -> None:
        mock_call.return_value = {"success": True, "data": {"total_count": 0, "items": []}}
        result = await GithubCodeSearch().execute(
            {"query": "this-thing-does-not-exist"},
            AgentSession(ticket_id=1),
        )
        self.assertTrue(result.success)
        self.assertIn("未找到", result.output)

    async def test_code_search_empty_query_rejected(self) -> None:
        result = await GithubCodeSearch().execute({"query": "  "}, AgentSession(ticket_id=1))
        self.assertFalse(result.success)
        self.assertIn("不能为空", result.error)

    async def test_code_search_rejects_invalid_limit(self) -> None:
        result = await GithubCodeSearch().execute(
            {"query": "foo", "limit": "many"},
            AgentSession(ticket_id=1),
        )
        self.assertFalse(result.success)
        self.assertIn("limit", result.error or "")

    async def test_code_search_rejects_invalid_optional_string_args(self) -> None:
        bad_language = await GithubCodeSearch().execute(
            {"query": "foo", "language": ["python"]},
            AgentSession(ticket_id=1),
        )
        bad_path = await GithubCodeSearch().execute(
            {"query": "foo", "path": {"prefix": "core/tools"}},
            AgentSession(ticket_id=1),
        )
        self.assertEqual(bad_language.error, "language must be a string")
        self.assertEqual(bad_path.error, "path must be a string")

    async def test_code_search_rejects_invalid_required_string_args(self) -> None:
        bad_query = await GithubCodeSearch().execute(
            {"query": ["InvalidCredential"]},
            AgentSession(ticket_id=1),
        )
        bad_scope = await GithubCodeSearch().execute(
            {"query": "InvalidCredential", "scope": {"org": "langgenius"}},
            AgentSession(ticket_id=1),
        )
        self.assertEqual(bad_query.error, "query must be a string")
        self.assertEqual(bad_scope.error, "scope must be a string")
