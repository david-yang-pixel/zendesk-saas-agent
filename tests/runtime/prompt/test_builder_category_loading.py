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


def test_sql_templates_always_loaded_regardless_of_category():
    """Per user mandate: 所有 prompt 段始终加载，不再按 category gate。"""
    out = _build(PromptContext(ticket_type="product_usage"))
    assert "SELECT" in out.upper()
    out2 = _build(PromptContext())  # no category at all
    assert "SELECT" in out2.upper()


def test_refund_flow_always_loaded_regardless_of_category():
    out_refund = _build(PromptContext(ticket_type="refund"))
    out_other = _build(PromptContext(ticket_type="workflow"))
    out_blank = _build(PromptContext())
    assert "退款工单处理流程" in out_refund
    assert "退款工单处理流程" in out_other
    assert "退款工单处理流程" in out_blank


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


def test_dev_report_always_loaded_regardless_of_category():
    out = _build(PromptContext(ticket_type="refund"))
    assert "开发转交格式" in out or "Dev Report" in out
    out_blank = _build(PromptContext())
    assert "开发转交格式" in out_blank or "Dev Report" in out_blank


# ─────────── DB schema runtime-loaded section ───────────

def test_db_schema_section_absent_when_file_missing(tmp_path, monkeypatch):
    """Schema 文件不存在时（开发环境）db_schema section 应优雅跳过。"""
    from zd_agent.runtime.prompt import defaults as defaults_mod

    monkeypatch.setattr(defaults_mod, "_db_schema_path", lambda: tmp_path / "nope.md")
    out = _build(PromptContext())
    # 文件不存在 → schema 内容不该出现
    assert "**用途**：Dify SaaS 主业务库" not in out


def test_db_schema_section_loaded_when_file_present(tmp_path, monkeypatch):
    """Schema 文件存在 → 内容注入 system prompt。"""
    from zd_agent.runtime.prompt import defaults as defaults_mod

    fake = (
        "# Dify SaaS Database Schema\n\n"
        "## langgenius\n\n"
        "**用途**：Dify SaaS 主业务库 (mocked)\n\n"
        "### `tenants`\n"
        "`id` uuid*, `name` varchar, `plan` varchar\n"
    )
    schema_file = tmp_path / "schema.md"
    schema_file.write_text(fake, encoding="utf-8")
    monkeypatch.setattr(defaults_mod, "_db_schema_path", lambda: schema_file)
    out = _build(PromptContext())
    assert "Dify SaaS 主业务库 (mocked)" in out
    assert "tenants" in out
    assert "uuid*" in out


def test_db_schema_section_re_reads_each_build(tmp_path, monkeypatch):
    """schema 文件 mid-flight 被刷新 → 下一次 build 读到新内容（而不是缓存旧内容）。"""
    from zd_agent.runtime.prompt import defaults as defaults_mod

    schema_file = tmp_path / "schema.md"
    schema_file.write_text(
        "# Dify SaaS Database Schema\n## langgenius\nTABLE_VERSION_1\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(defaults_mod, "_db_schema_path", lambda: schema_file)
    out1 = _build(PromptContext())
    assert "TABLE_VERSION_1" in out1

    # 模拟 cron 刷了一次 schema
    schema_file.write_text(
        "# Dify SaaS Database Schema\n## langgenius\nTABLE_VERSION_2\n",
        encoding="utf-8",
    )
    out2 = _build(PromptContext())
    assert "TABLE_VERSION_2" in out2
    assert "TABLE_VERSION_1" not in out2
