import unittest

from zd_agent.session.agent_session import AgentSession
from zd_agent.tools.official_docs import (
    OfficialDocEntry,
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
    async def test_search_rejects_invalid_limit(self) -> None:
        tool = OfficialDocsSearch()

        result = await tool.execute(
            {"query": "helm", "limit": "many"},
            AgentSession(ticket_id=1),
        )

        self.assertFalse(result.success)
        self.assertIn("limit", result.error or "")


if __name__ == "__main__":
    unittest.main()
