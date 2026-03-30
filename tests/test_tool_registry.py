import unittest

from zd_agent.tools.registry import create_tool_registry


class TestToolRegistry(unittest.TestCase):
    def test_registers_github_pr_tools(self) -> None:
        registry = create_tool_registry()
        self.assertIn("github_search_prs", registry)
        self.assertIn("github_get_pr", registry)
