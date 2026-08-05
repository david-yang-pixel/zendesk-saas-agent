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
    assert "先调用 `zendesk_get_ticket`" in prompt
    assert "读取成功后再调用 `classify_ticket`" in prompt
    assert "不能与 `zendesk_get_ticket` 并行调用" in prompt


def test_core_router_requires_structured_plan_before_evidence_tools():
    prompt = _build()

    assert "调用 `plan_investigation`" in prompt
    assert "计划成功前不要调用 DB、SigNoz、Source、GitHub 或 subagent" in prompt
    assert "customer_question" in prompt
    assert "execution_chain" in prompt
    assert "independent_lanes" in prompt


def test_core_router_binds_material_claims_to_exact_tool_call_ids():
    prompt = _build()

    assert "source_call_ids" in prompt
    assert "精确成功调用 ID" in prompt
    assert "只写 source_tools 工具名不能证明 claim" in prompt


def test_core_router_exhausts_support_side_evidence_before_asking_customer():
    prompt = _build()

    assert "先穷尽当前支持侧可取得的证据" in prompt
    assert "无法从现有身份、对象和工具结果推导" in prompt


def test_prompt_forbids_source_paths_in_customer_visible_replies():
    prompt = _build()

    assert "客户可见回复不得包含源码路径" in prompt
    assert "文件名+行号" in prompt
    assert "源码路径、SQL、日志、trace、内部判断只能写入 internal note" in prompt


def test_prompt_requires_professional_readable_public_replies_and_internal_analysis():
    prompt = _build()

    assert "资深技术支持工程师" in prompt
    assert "不用 Markdown 标题、粗体、反引号、emoji 或装饰性分隔线" in prompt
    assert "公开回复成功后，系统会自动写入一条内部分析" in prompt
    assert "evidence_ledger" in prompt
    assert "已确认根因" in prompt


def test_prompt_requires_customer_update_before_human_or_sre_escalation():
    prompt = _build()

    assert "需要 SRE、工程师、财务或人工审核时，先调用 `zendesk_public_reply`" in prompt
    assert "公开说明已确认事实、升级原因和下一步" in prompt
    assert "只有公开回复通道不可用，或说明原因本身会泄露安全或隐私信息时" in prompt


def test_prompt_leaves_handoff_support_hours_to_the_send_layer():
    prompt = _build()

    assert "上海时间 09:00（含）到 18:00（不含）" in prompt
    assert "handoff 的工作时间状态由发送层自动追加" in prompt
    assert "不要在 body 里重复写工作时间" in prompt


def test_prompt_forbids_extra_feishu_push_after_handoff():
    prompt = _build()

    assert "reply_type=handoff" in prompt
    assert "不要额外调用 `feishu_push`" in prompt


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

    assert len(prompt) < 16_000
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
