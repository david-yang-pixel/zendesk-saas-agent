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


def test_prompt_dispatches_subagents_for_multi_source_investigations():
    prompt = _build()

    assert "多证据源工单默认派 subagent" in prompt
    assert "代码 / 文档 / 数据库 / 日志 / 历史工单 / GitHub PR" in prompt
    assert "最多 5 个 subagent" in prompt
    assert "完成判定" in prompt
    assert "不要主动加载" in prompt
    assert "sandbox_read 加载" not in prompt
    assert "默认不派 subagent" not in prompt


def test_prompt_requires_structured_ticket_done_evidence_blocks():
    prompt = _build()

    assert "数据库核查" in prompt
    assert "代码路径" in prompt
    assert "日志核查" in prompt
    assert "不要把 DB 字段写进一整段英文长句" in prompt
    assert "**workspace**" in prompt
    assert "- `status`: normal" in prompt
    assert "`字段`: 值" in prompt
