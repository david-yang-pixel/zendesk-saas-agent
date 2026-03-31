import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class TestPrompts(unittest.TestCase):
    def test_agents_prompt_requires_step_5_5(self) -> None:
        content = (ROOT / "src/zd_agent/prompts/AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("Step 5.5", content)
        self.assertIn("修复状态检查", content)

    def test_agents_prompt_requires_three_sections(self) -> None:
        content = (ROOT / "src/zd_agent/prompts/AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("内部分析", content)
        self.assertIn("客户回复建议", content)
        self.assertIn("人工复核指引", content)

    def test_code_bug_prompt_requires_github_pr_check(self) -> None:
        content = (ROOT / "src/zd_agent/prompts/strategies/code-bug.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("GitHub", content)
        self.assertIn("open / merged PR", content)
        self.assertIn("已合并未发布", content)

    def test_tools_prompt_mentions_github_pr_tools(self) -> None:
        content = (ROOT / "src/zd_agent/prompts/TOOLS.md").read_text(encoding="utf-8")
        self.assertIn("github_search_prs", content)
        self.assertIn("github_get_pr", content)

    def test_agents_prompt_prioritizes_language_hint_from_ticket_tool(self) -> None:
        content = (ROOT / "src/zd_agent/prompts/AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("回复语言建议", content)
        self.assertIn("不要被系统提示语言影响", content)


if __name__ == "__main__":
    unittest.main()
