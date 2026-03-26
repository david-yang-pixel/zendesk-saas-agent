import unittest

from zd_agent.runtime.source_selector import suggest_sources


class SourceSelectorTest(unittest.TestCase):
    def test_enterprise_sso_ticket_prefers_enterprise_docs(self) -> None:
        result = suggest_sources(
            subject="SSO 登录失败，Okta OIDC 报错",
            description="企业版环境下配置 Okta OIDC 后无法登录",
            product="dify-enterprise",
            tags=["sso", "okta"],
            custom_fields={
                "Product": "Dify Enterprise",
                "Environment": "production",
            },
        )

        self.assertEqual(result[0].source, "official_docs_enterprise")
        self.assertIn("zendesk_kb", [item.source for item in result])
        self.assertIn("source_code", [item.source for item in result])

    def test_api_ticket_prefers_community_docs(self) -> None:
        result = suggest_sources(
            subject="Chat API 返回 401",
            description="调用 /v1/chat-messages 接口时鉴权失败",
            product="dify",
            tags=["api"],
            custom_fields={"Product": "Dify Community"},
        )

        self.assertEqual(result[0].source, "official_docs_community")
        self.assertIn("zendesk_kb", [item.source for item in result])
        self.assertNotIn("official_docs_enterprise", [item.source for item in result[:2]])

    def test_deployment_ticket_keeps_helm_watchdog_and_docs_together(self) -> None:
        result = suggest_sources(
            subject="Helm 升级后环境变量未生效",
            description="chart 升级到 3.0.x 后 deployment 配置异常",
            product="dify-enterprise",
            tags=["helm", "deployment"],
            custom_fields={
                "Product": "Dify Enterprise",
                "Version": "3.0.x",
            },
        )

        top_sources = [item.source for item in result[:3]]
        self.assertIn("helm_watchdog", top_sources)
        self.assertIn("official_docs_enterprise", top_sources)


if __name__ == "__main__":
    unittest.main()
