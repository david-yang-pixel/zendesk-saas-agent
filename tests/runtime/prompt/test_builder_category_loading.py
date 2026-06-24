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


# ─────────── SQL/DB/REFUND lazy-load (no longer in system prompt) ───────────
# 改动 3: 这三段从 system prompt 移除，改由 classify_ticket 工具按 ticket_type
# 选择性附加到 tool result。避免在不需要的工单上浪费 ~9-10k tokens。

def test_sql_templates_NOT_in_system_prompt():
    """SQL_TEMPLATES 不再出现在 system prompt（lazy 注入到 classify tool result）。"""
    out = _build(PromptContext(ticket_type="workflow"))
    out2 = _build(PromptContext())
    # SQL_TEMPLATES.md 里有非常具体的 marker；用它判断是否真的在 system prompt
    # 里。"SELECT" 太宽泛，COMMON_PLAYBOOK / DEV_REPORT 里也可能出现。
    assert "## SQL 查询模板" not in out
    assert "## SQL 查询模板" not in out2


def test_refund_flow_NOT_in_system_prompt():
    """REFUND_FLOW 不再出现在 system prompt（lazy 注入到 classify tool result）。"""
    out_refund = _build(PromptContext(ticket_type="refund"))
    out_other = _build(PromptContext(ticket_type="workflow"))
    out_blank = _build(PromptContext())
    assert "退款工单处理流程" not in out_refund
    assert "退款工单处理流程" not in out_other
    assert "退款工单处理流程" not in out_blank


def test_classify_ticket_attaches_sql_for_sql_categories():
    """ticket_type ∈ NEEDS_SQL → classify tool 输出含 SQL_TEMPLATES。"""
    import asyncio
    from zd_agent.session.agent_session import AgentSession
    from zd_agent.tools.classify import ClassifyTicket

    tool = ClassifyTicket()
    session = AgentSession(ticket_id=1)
    result = asyncio.get_event_loop().run_until_complete(
        tool.execute({"ticket_type": "workflow", "reasoning": "test"}, session)
    )
    assert result.success is True
    assert "SQL 查询模板" in (result.output or "")


def test_classify_ticket_skips_sql_for_non_sql_categories():
    """ticket_type ∉ NEEDS_SQL → classify tool 输出不附加 SQL_TEMPLATES。"""
    import asyncio
    from zd_agent.session.agent_session import AgentSession
    from zd_agent.tools.classify import ClassifyTicket

    tool = ClassifyTicket()
    session = AgentSession(ticket_id=1)
    result = asyncio.get_event_loop().run_until_complete(
        tool.execute({"ticket_type": "product_usage", "reasoning": "test"}, session)
    )
    assert result.success is True
    assert "SQL 查询模板" not in (result.output or "")
    assert "退款流程" not in (result.output or "")


def test_classify_ticket_attaches_refund_only_for_refund():
    """REFUND_FLOW 只在 refund 工单出现。"""
    import asyncio
    from zd_agent.session.agent_session import AgentSession
    from zd_agent.tools.classify import ClassifyTicket

    tool = ClassifyTicket()
    session = AgentSession(ticket_id=1)
    refund_out = asyncio.get_event_loop().run_until_complete(
        tool.execute({"ticket_type": "refund", "reasoning": "test"}, session)
    )
    workflow_out = asyncio.get_event_loop().run_until_complete(
        tool.execute({"ticket_type": "workflow", "reasoning": "test"}, session)
    )
    assert "退款流程" in (refund_out.output or "")
    assert "退款流程" not in (workflow_out.output or "")


def test_followup_context_injected_when_mode_followup():
    out = _build(PromptContext(
        mode="followup",
        previous_record="# 工单 123\n\n上次分析：XYZ",
        new_comments_diff="客户补充：ABC",
    ))
    assert "跟进模式" in out
    assert "Zendesk 最新对话是唯一真相源" in out
    assert "上次分析：XYZ" in out
    assert "客户补充：ABC" in out


def test_followup_context_skipped_when_mode_first_pass():
    out = _build(PromptContext(
        mode="first_pass",
        previous_record="should be ignored",
    ))
    assert "跟进模式 · 历史分析记录" not in out


def test_followup_context_skipped_without_verified_new_diff():
    out = _build(PromptContext(
        mode="followup",
        previous_record="old local record",
        new_comments_diff="",
    ))
    assert "跟进模式 · 最新 Zendesk 对话优先" not in out


def test_dev_report_loaded_for_platform_issue():
    out = _build(PromptContext(ticket_type="platform_issue"))
    # DEV_REPORT content has a distinctive header
    assert "开发转交格式" in out or "Dev Report" in out


def test_dev_report_always_loaded_regardless_of_category():
    out = _build(PromptContext(ticket_type="refund"))
    assert "开发转交格式" in out or "Dev Report" in out
    out_blank = _build(PromptContext())
    assert "开发转交格式" in out_blank or "Dev Report" in out_blank


# ─────────── DB schema lazy-loaded via classify tool ───────────
# 改动 3: DB_SCHEMA 也从 system prompt 移除，跟 SQL_TEMPLATES 一起在
# classify_ticket 工具按 ticket_type 注入到 tool result。

def test_db_schema_NOT_in_system_prompt(tmp_path, monkeypatch):
    """DB_SCHEMA 不再出现在 system prompt（即使文件存在也是）。"""
    from zd_agent.runtime.prompt import defaults as defaults_mod

    fake = (
        "# Dify SaaS Database Schema\n\n"
        "## langgenius\n\n**用途**：Dify SaaS 主业务库 (mocked)\n"
    )
    schema_file = tmp_path / "schema.md"
    schema_file.write_text(fake, encoding="utf-8")
    monkeypatch.setattr(defaults_mod, "_db_schema_path", lambda: schema_file)
    out = _build(PromptContext())
    assert "Dify SaaS 主业务库 (mocked)" not in out


def test_classify_ticket_attaches_db_schema_for_sql_categories(tmp_path, monkeypatch):
    """ticket_type ∈ NEEDS_SQL 且 schema 文件存在 → classify tool 输出含 schema 内容。"""
    import asyncio
    from zd_agent.runtime.prompt import defaults as defaults_mod
    from zd_agent.session.agent_session import AgentSession
    from zd_agent.tools.classify import ClassifyTicket

    fake = (
        "# Dify SaaS Database Schema\n\n"
        "## langgenius\n\n**用途**：Dify SaaS 主业务库 (mocked)\n"
    )
    schema_file = tmp_path / "schema.md"
    schema_file.write_text(fake, encoding="utf-8")
    monkeypatch.setattr(defaults_mod, "_db_schema_path", lambda: schema_file)

    tool = ClassifyTicket()
    session = AgentSession(ticket_id=1)
    result = asyncio.get_event_loop().run_until_complete(
        tool.execute({"ticket_type": "workflow", "reasoning": "test"}, session)
    )
    assert "Dify SaaS 主业务库 (mocked)" in (result.output or "")


def test_classify_ticket_skips_db_schema_when_file_missing(tmp_path, monkeypatch):
    """schema 文件不存在 → 不附加 DB_SCHEMA 段（其他 SQL_TEMPLATES 仍正常）。"""
    import asyncio
    from zd_agent.runtime.prompt import defaults as defaults_mod
    from zd_agent.session.agent_session import AgentSession
    from zd_agent.tools.classify import ClassifyTicket

    monkeypatch.setattr(defaults_mod, "_db_schema_path", lambda: tmp_path / "nope.md")

    tool = ClassifyTicket()
    session = AgentSession(ticket_id=1)
    result = asyncio.get_event_loop().run_until_complete(
        tool.execute({"ticket_type": "workflow", "reasoning": "test"}, session)
    )
    # SQL 模板还在
    assert "SQL 查询模板" in (result.output or "")
    # DB schema 段缺失
    assert "生产库 Schema 速查" not in (result.output or "")
