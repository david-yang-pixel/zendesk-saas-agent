from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
REFUND_STRATEGY_PROMPT = ROOT / "src/zd_agent/prompts/strategies/refund.md"
REFUND_FLOW_PROMPT = ROOT / "src/zd_agent/prompts/refund/REFUND_FLOW.md"
SOUL_PROMPT = ROOT / "src/zd_agent/prompts/SOUL.md"
PLATFORM_PROMPT = ROOT / "src/zd_agent/prompts/strategies/platform_issue.md"


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


def test_github_pr_gate_requires_candidate_validation() -> None:
    text = SOUL_PROMPT.read_text(encoding="utf-8")

    assert "候选修复 PR" in text
    assert "是否被 revert" in text
    assert "follow-up regression" in text
    assert "客户当前版本是否已包含修复" in text
    assert "不要建议升级到该修复版" in text


def test_platform_issue_prompt_treats_batch_tickets_as_incident_signal() -> None:
    text = PLATFORM_PROMPT.read_text(encoding="utf-8")

    assert "30 分钟内" in text
    assert ">=2 个同类工单" in text
    assert "platform incident" in text
    assert "zendesk_search" in text
    assert "全局指标" in text
    assert "升级 SRE" in text
