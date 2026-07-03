from zd_agent import config as config_module
from zd_agent.config import AppConfig
from zd_agent.runtime.prompt.builder import PromptBuilder
from zd_agent.runtime.prompt.defaults import register_default_sections
from zd_agent.runtime.prompt.section import PromptContext


def _build() -> str:
    builder = PromptBuilder()
    register_default_sections(builder)
    return builder.build(PromptContext())


def test_core_router_requires_classify_ticket_first():
    prompt = _build()

    assert "Dify SaaS Support Agent Core Router" in prompt
    assert "第一轮必须尽早调用 `classify_ticket`" in prompt
    assert "该工具返回本工单要遵循的 Skills" in prompt


def test_prompt_forbids_source_paths_in_customer_visible_replies():
    prompt = _build()

    assert "客户可见回复不得包含源码路径" in prompt
    assert "文件名+行号" in prompt
    assert "源码路径、SQL、日志、trace、内部判断只能写入 internal note" in prompt


def test_core_router_requires_remote_tool_services():
    prompt = _build()

    assert "均走远程 MCP/Tool Service" in prompt
    assert "不要回退到本地 CLI" in prompt
    assert "客户可见 public reply 走 Browser Relay" in prompt


def test_prompt_requires_github_issue_pr_gate():
    prompt = _build()

    assert "GitHub PR 候选必须确认是否被 revert" in prompt
    assert "客户当前版本是否已包含修复" in prompt


def test_prompt_keeps_zendesk_as_followup_source_of_truth():
    prompt = _build()

    assert "Zendesk 最新对话是唯一真相源" in prompt
    assert "本地工单记录只作为参考" in prompt


def test_prompt_requires_attachment_download_for_image_or_file_claims():
    prompt = _build()

    assert "客户提到截图、图片、文件或附件时" in prompt
    assert "必须调用 `zendesk_get_attachments`" in prompt
    assert "并下载附件后再判断" in prompt


def test_core_router_keeps_legacy_bulk_sections_out_of_system_prompt():
    prompt = _build()

    assert len(prompt) < 5_000
    assert "高频工单快速分诊" not in prompt
    assert "客户回复模板" not in prompt
    assert "开发转交格式" not in prompt


def test_admin_agent_prompt_override_is_injected(monkeypatch):
    cfg = AppConfig()
    cfg.prompts.agent_extra_instructions = "Use admin-managed escalation wording."
    monkeypatch.setattr(config_module, "_config", cfg)

    prompt = _build()

    assert "## 管理后台系统提示词" in prompt
    assert "Use admin-managed escalation wording." in prompt
