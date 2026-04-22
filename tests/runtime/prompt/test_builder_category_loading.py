"""Verify SaaS prompt section conditional loading."""
from datetime import datetime

from zd_agent.runtime.prompt.builder import PromptBuilder
from zd_agent.runtime.prompt.defaults import register_default_sections
from zd_agent.runtime.prompt.section import PromptContext


def _build(ctx: PromptContext) -> str:
    b = PromptBuilder()
    register_default_sections(b)
    return b.build(ctx)


def test_common_playbook_always_loaded():
    out = _build(PromptContext())
    assert "高频工单快速分诊" in out or "快速通道" in out


def test_reply_templates_always_loaded():
    out = _build(PromptContext())
    assert "客户回复模板" in out or "Missing Critical Info" in out


def test_sql_templates_loaded_for_workflow_category():
    out = _build(PromptContext(ticket_type="workflow"))
    # Template file contains SQL heading markers
    assert "SELECT" in out.upper()


def test_sql_templates_skipped_for_product_usage():
    """product_usage category shouldn't pull in the SQL templates."""
    out = _build(PromptContext(ticket_type="product_usage"))
    # The SQL_TEMPLATES file is not loaded; assert on a string unique to SQL_TEMPLATES header
    assert "常用 SQL 查询模板" not in out


def test_refund_flow_only_for_refund():
    out_refund = _build(PromptContext(ticket_type="refund"))
    out_other = _build(PromptContext(ticket_type="workflow"))
    assert "退款工单处理流程" in out_refund
    assert "退款工单处理流程" not in out_other


def test_followup_context_injected_when_mode_followup():
    out = _build(PromptContext(
        mode="followup",
        previous_record="# 工单 123\n\n上次分析：XYZ",
        new_comments_diff="客户补充：ABC",
    ))
    assert "跟进模式" in out
    assert "上次分析：XYZ" in out
    assert "客户补充：ABC" in out


def test_followup_context_skipped_when_mode_first_pass():
    out = _build(PromptContext(
        mode="first_pass",
        previous_record="should be ignored",
    ))
    assert "跟进模式 · 历史分析记录" not in out


def test_dev_report_loaded_for_platform_issue():
    out = _build(PromptContext(ticket_type="platform_issue"))
    # DEV_REPORT content has a distinctive header
    assert "开发转交格式" in out or "Dev Report" in out


def test_dev_report_skipped_for_refund():
    out = _build(PromptContext(ticket_type="refund"))
    assert "开发转交格式" not in out
