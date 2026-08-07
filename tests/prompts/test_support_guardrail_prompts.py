from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
REFUND_STRATEGY_PROMPT = ROOT / "src/zd_agent/prompts/strategies/refund.md"
REFUND_FLOW_PROMPT = ROOT / "src/zd_agent/prompts/refund/REFUND_FLOW.md"
SOUL_PROMPT = ROOT / "src/zd_agent/prompts/SOUL.md"
PLATFORM_PROMPT = ROOT / "src/zd_agent/prompts/strategies/platform_issue.md"
WORKFLOW_PROMPT = ROOT / "src/zd_agent/prompts/strategies/workflow.md"
AGENTS_PROMPT = ROOT / "src/zd_agent/prompts/AGENTS.md"
DIFY_SUPPORT_PROMPT = ROOT / "src/zd_agent/prompts/skills/DIFY_SUPPORT_TICKET.md"
TOOLS_PROMPT = ROOT / "src/zd_agent/prompts/TOOLS.md"
QUALITY_BAR_PROMPT = ROOT / "src/zd_agent/prompts/references/quality_bar.md"
BILLING_PROMPT = ROOT / "src/zd_agent/prompts/strategies/billing.md"
IDENTITY_STRATEGY_PROMPTS = (
    BILLING_PROMPT,
    ROOT / "src/zd_agent/prompts/strategies/permissions.md",
    ROOT / "src/zd_agent/prompts/strategies/knowledge_base.md",
    ROOT / "src/zd_agent/prompts/strategies/api.md",
    ROOT / "src/zd_agent/prompts/strategies/platform_issue.md",
    ROOT / "src/zd_agent/prompts/strategies/workflow.md",
)


def test_refund_prompts_require_owner_gate_before_eligibility() -> None:
    strategy = REFUND_STRATEGY_PROMPT.read_text(encoding="utf-8")
    flow = REFUND_FLOW_PROMPT.read_text(encoding="utf-8")

    for text in (strategy, flow):
        assert "Owner gate" in text
        assert "requester" in text
        assert "workspace owner" in text
        assert "tenant_account_joins" in text
        assert "不是 owner" in text
        assert "不要继续判断退款资格" in text
        assert "不要推送飞书" in text
        assert "不要 `ticket_escalate`" in text
        assert "人工业务审批" in text
        assert "不能自行判定" in text


def test_github_pr_gate_requires_candidate_validation() -> None:
    text = SOUL_PROMPT.read_text(encoding="utf-8")

    assert "候选修复 PR" in text
    assert "是否被 revert" in text
    assert "follow-up regression" in text
    assert "客户当前版本是否已包含修复" in text
    assert "不要建议升级到该修复版" in text


def test_workflow_prompt_does_not_turn_latency_correlation_into_root_cause() -> None:
    text = WORKFLOW_PROMPT.read_text(encoding="utf-8")

    assert "不能据此直接写成 provider 侧根因" in text
    assert "仍需继续排查" in text
    assert "常见根因（按概率排序）" not in text


def test_platform_issue_prompt_treats_batch_tickets_as_incident_signal() -> None:
    text = PLATFORM_PROMPT.read_text(encoding="utf-8")

    assert "30 分钟内" in text
    assert ">=2 个同类工单" in text
    assert "platform incident" in text
    assert "zendesk_search" in text
    assert "全局指标" in text
    assert "升级 SRE" in text


def test_all_escalation_prompts_require_a_customer_safe_public_update_first() -> None:
    agents = AGENTS_PROMPT.read_text(encoding="utf-8")
    support = DIFY_SUPPORT_PROMPT.read_text(encoding="utf-8")
    tools = TOOLS_PROMPT.read_text(encoding="utf-8")
    platform = PLATFORM_PROMPT.read_text(encoding="utf-8")
    refund_strategy = REFUND_STRATEGY_PROMPT.read_text(encoding="utf-8")
    refund_flow = REFUND_FLOW_PROMPT.read_text(encoding="utf-8")

    assert "先公开回复客户" in agents
    assert "先公开回复客户" in support
    assert "需要人工或 SRE 介入时，先用 `zendesk_public_reply`" in tools
    assert "升级 SRE 前先公开回复客户" in platform
    assert "低置信度也要先公开说明" in refund_strategy
    assert "低置信度也要先公开说明" in refund_flow

    assert "不可重现 platform_issue 需 SRE drill down | `zendesk_internal_reply`" not in agents
    assert "改走 `zendesk_internal_reply`" not in refund_strategy
    assert "什么情况依然 escalate 不公开回复" not in refund_flow


def test_support_prompts_do_not_force_irrelevant_evidence_or_local_mirrors() -> None:
    soul = SOUL_PROMPT.read_text(encoding="utf-8")
    tools = TOOLS_PROMPT.read_text(encoding="utf-8")
    quality = QUALITY_BAR_PROMPT.read_text(encoding="utf-8")

    combined = "\n".join((soul, tools, quality))
    assert "多证据源工单默认派 subagent" not in combined
    assert "有 Workspace ID 时**必跑万能起手式**" not in combined
    assert "archive_ticket" not in combined
    assert "只有当前结论确实依赖 DB" in combined
    assert "根因 confirmed 才写明确因果" in combined


def test_billing_prompts_bind_form_email_workspace_to_billing_tenant() -> None:
    soul = SOUL_PROMPT.read_text(encoding="utf-8")
    billing = BILLING_PROMPT.read_text(encoding="utf-8")

    for text in (soul, billing):
        assert "表单 Email" in text
        assert "section=tenants" in text
        assert "Workspace ID 是邮箱格式" in text
        assert "显式目标" in text
        assert "同时查询表单 Email" in text
        assert "多个 workspace" in text
        assert "禁止猜测" in text


def test_active_prompts_treat_workspace_identity_as_support_side_resolution() -> None:
    agents = AGENTS_PROMPT.read_text(encoding="utf-8")
    support = DIFY_SUPPORT_PROMPT.read_text(encoding="utf-8")

    for text in (agents, support):
        assert "邮箱唯一映射时禁止再向客户索取 Workspace ID 或 workspace name" in text
        assert "内部查询未执行、失败或未验证" in text

    for path in IDENTITY_STRATEGY_PROMPTS:
        text = path.read_text(encoding="utf-8")
        assert "收集：Workspace ID" not in text
        assert "邮箱唯一映射" in text


def test_support_contract_requires_planned_bounded_subagents_and_call_ids() -> None:
    agents = AGENTS_PROMPT.read_text(encoding="utf-8")
    support = DIFY_SUPPORT_PROMPT.read_text(encoding="utf-8")
    tools = TOOLS_PROMPT.read_text(encoding="utf-8")

    for text in (agents, support, tools):
        assert "plan_investigation" in text
        assert "source_call_ids" in text
        assert "independent_lanes" in text
    assert "父 Agent 不得重复查询子 Agent 已返回的同一证据" in support
