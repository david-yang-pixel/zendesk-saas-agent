"""Prompt guardrails that mirror the local Dify support skill."""

from zd_agent.runtime.prompt.builder import PromptBuilder
from zd_agent.runtime.prompt.defaults import register_default_sections
from zd_agent.runtime.prompt.section import PromptContext


def _build() -> str:
    builder = PromptBuilder()
    register_default_sections(builder)
    return builder.build(PromptContext())


def test_prompt_requires_source_verified_claims():
    prompt = _build()

    assert "源码 / API / 配置 / 字段 / UI 路径 / PR / 版本" in prompt
    assert "git_grep / git_show" in prompt
    assert "Source MCP runtime" in prompt


def test_prompt_requires_github_issue_pr_gate():
    prompt = _build()

    assert "GitHub Issue / PR evidence gate" in prompt
    assert "不能只读源码就给客户下结论" in prompt


def test_prompt_keeps_zendesk_as_followup_source_of_truth():
    prompt = _build()

    assert "Zendesk 最新对话是唯一真相源" in prompt
    assert "本地工单记录只作为参考" in prompt
