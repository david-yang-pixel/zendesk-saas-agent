import unittest

from zd_agent.tools.registry import create_tool_registry


class TestToolRegistry(unittest.TestCase):
    def test_registers_github_tools(self) -> None:
        registry = create_tool_registry()
        self.assertIn("github_search_prs", registry)
        self.assertIn("github_get_pr", registry)
        self.assertIn("github_search_issues", registry)
        self.assertIn("github_get_issue", registry)
        self.assertIn("github_code_search", registry)

    def test_does_not_register_local_cli_tools(self) -> None:
        registry = create_tool_registry()

        self.assertNotIn("zd_kb_cli_exec", registry)
