import unittest
from unittest.mock import AsyncMock, patch

from zd_agent.session.agent_session import AgentSession
from zd_agent.tools.official_docs import (
    OfficialDocEntry,
    OfficialDocsRead,
    OfficialDocsSearch,
    parse_llms_index,
    search_doc_entries,
)


class OfficialDocsTest(unittest.TestCase):
    def test_parse_llms_index_extracts_entries(self) -> None:
        content = """
# Dify Docs

## Docs

- [Dify Helm Chart](https://enterprise-docs.dify.ai/versions/3-0-x/zh-cn/deployment/advanced-configuration/dify-helm-chart.md): Helm 部署说明
- [环境变量说明](https://enterprise-docs.dify.ai/versions/3-0-x/zh-cn/deployment/advanced-configuration/environment-variables.md): 环境变量配置
""".strip()

        entries = parse_llms_index(content, source="official_docs_enterprise")

        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0].title, "Dify Helm Chart")
        self.assertEqual(
            entries[0].url,
            "https://enterprise-docs.dify.ai/versions/3-0-x/zh-cn/deployment/advanced-configuration/dify-helm-chart.md",
        )
        self.assertEqual(entries[0].source, "official_docs_enterprise")

    def test_search_doc_entries_prefers_title_and_description_matches(self) -> None:
        entries = [
            OfficialDocEntry(
                title="Dify Helm Chart",
                url="https://enterprise-docs.dify.ai/helm.md",
                description="Helm 部署说明",
                source="official_docs_enterprise",
            ),
            OfficialDocEntry(
                title="环境变量说明",
                url="https://enterprise-docs.dify.ai/env.md",
                description="部署环境变量配置",
                source="official_docs_enterprise",
            ),
            OfficialDocEntry(
                title="聊天助手",
                url="https://docs.dify.ai/chatbot.md",
                description="应用编排",
                source="official_docs_community",
            ),
        ]

        matches = search_doc_entries(entries, query="helm 部署", limit=2)

        self.assertEqual(len(matches), 2)
        self.assertEqual(matches[0].title, "Dify Helm Chart")
        self.assertEqual(matches[1].title, "环境变量说明")


class OfficialDocsSearchTest(unittest.IsolatedAsyncioTestCase):
    async def test_search_rejects_missing_query(self) -> None:
        tool = OfficialDocsSearch()

        result = await tool.execute(
            {},
            AgentSession(ticket_id=1),
        )

        self.assertFalse(result.success)
        self.assertEqual(result.error, "query is required")

    async def test_search_rejects_blank_query(self) -> None:
        tool = OfficialDocsSearch()

        with patch("zd_agent.tools.official_docs.call_docs_tool", new_callable=AsyncMock) as call_docs:
            result = await tool.execute(
                {"query": "   "},
                AgentSession(ticket_id=1),
            )

        self.assertFalse(result.success)
        self.assertEqual(result.error, "query is empty")
        call_docs.assert_not_called()

    async def test_search_rejects_invalid_limit(self) -> None:
        tool = OfficialDocsSearch()

        result = await tool.execute(
            {"query": "helm", "limit": "many"},
            AgentSession(ticket_id=1),
        )

        self.assertFalse(result.success)
        self.assertIn("limit", result.error or "")

    async def test_search_rejects_non_string_query(self) -> None:
        tool = OfficialDocsSearch()

        with patch("zd_agent.tools.official_docs.call_docs_tool", new_callable=AsyncMock):
            result = await tool.execute(
                {"query": {"text": "helm"}},
                AgentSession(ticket_id=1),
            )

        self.assertFalse(result.success)
        self.assertEqual(result.error, "query must be a string")

    async def test_search_rejects_non_string_source(self) -> None:
        tool = OfficialDocsSearch()

        result = await tool.execute(
            {"query": "helm", "source": ["official_docs_enterprise"]},
            AgentSession(ticket_id=1),
        )

        self.assertFalse(result.success)
        self.assertEqual(result.error, "source must be a string")

    @patch("zd_agent.tools.official_docs.call_docs_tool", new_callable=AsyncMock)
    async def test_search_uses_docs_tool_service_and_formats_results(
        self,
        call_docs: AsyncMock,
    ) -> None:
        call_docs.return_value = {
            "success": True,
            "data": {
                "results": [
                    {
                        "title": "Dify Helm Chart",
                        "url": "https://enterprise-docs.dify.ai/helm.md",
                        "description": "Helm 部署说明",
                        "source": "official_docs_enterprise",
                    }
                ]
            },
        }

        result = await OfficialDocsSearch().execute(
            {"query": "helm", "source": "official_docs_enterprise", "limit": 3},
            AgentSession(ticket_id=1),
        )

        self.assertTrue(result.success)
        self.assertIn("# 官方文档搜索结果", result.output)
        self.assertIn("Dify Helm Chart", result.output)
        call_docs.assert_awaited_once_with(
            "official_docs_search",
            {"query": "helm", "source": "official_docs_enterprise", "limit": 3},
        )

    @patch("zd_agent.tools.official_docs.call_docs_tool", new_callable=AsyncMock)
    async def test_search_surfaces_docs_tool_service_failure(
        self,
        call_docs: AsyncMock,
    ) -> None:
        call_docs.return_value = {
            "success": False,
            "code": "DOCS_TOOL_SERVICE_URL_MISSING",
            "error": "not configured",
        }

        result = await OfficialDocsSearch().execute(
            {"query": "helm"},
            AgentSession(ticket_id=1),
        )

        self.assertFalse(result.success)
        self.assertIn("DOCS_TOOL_SERVICE_URL_MISSING", result.error or "")


class OfficialDocsReadTest(unittest.IsolatedAsyncioTestCase):
    async def test_read_rejects_missing_url(self) -> None:
        tool = OfficialDocsRead()

        result = await tool.execute(
            {},
            AgentSession(ticket_id=1),
        )

        self.assertFalse(result.success)
        self.assertEqual(result.error, "url is required")

    async def test_read_rejects_blank_url(self) -> None:
        tool = OfficialDocsRead()

        result = await tool.execute(
            {"url": "   "},
            AgentSession(ticket_id=1),
        )

        self.assertFalse(result.success)
        self.assertEqual(result.error, "url is empty")

    async def test_read_rejects_non_string_url(self) -> None:
        tool = OfficialDocsRead()

        result = await tool.execute(
            {"url": {"href": "https://docs.dify.ai/"}},
            AgentSession(ticket_id=1),
        )

        self.assertFalse(result.success)
        self.assertEqual(result.error, "url must be a string")

    async def test_read_rejects_non_dify_docs_url(self) -> None:
        result = await OfficialDocsRead().execute(
            {"url": "https://example.com/page"},
            AgentSession(ticket_id=1),
        )

        self.assertFalse(result.success)
        self.assertEqual(result.error, "仅允许读取 Dify 官方文档站点")

    @patch("zd_agent.tools.official_docs.call_docs_tool", new_callable=AsyncMock)
    async def test_read_uses_docs_tool_service(self, call_docs: AsyncMock) -> None:
        call_docs.return_value = {
            "success": True,
            "data": {"url": "https://docs.dify.ai/hello", "content": "hello docs"},
        }

        result = await OfficialDocsRead().execute(
            {"url": "https://docs.dify.ai/hello"},
            AgentSession(ticket_id=1),
        )

        self.assertTrue(result.success)
        self.assertIn("# 官方文档", result.output)
        self.assertIn("hello docs", result.output)
        call_docs.assert_awaited_once_with(
            "official_docs_read",
            {"url": "https://docs.dify.ai/hello"},
        )


if __name__ == "__main__":
    unittest.main()
