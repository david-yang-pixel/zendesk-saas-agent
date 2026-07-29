"""Verify SaaS prompt section conditional loading."""

from zd_agent.runtime.prompt.builder import PromptBuilder
from zd_agent.runtime.prompt.defaults import register_default_sections
from zd_agent.runtime.prompt.section import PromptContext
from zd_agent.session.agent_session import AgentSession, TicketContext


def _build(ctx: PromptContext) -> str:
    b = PromptBuilder()
    register_default_sections(b)
    return b.build(ctx)


def _ticket_session(ticket_id: int = 1) -> AgentSession:
    return AgentSession(
        ticket_id=ticket_id,
        context=TicketContext(ticket_id=ticket_id, subject="Current ticket"),
    )


def test_core_router_always_loaded():
    out = _build(PromptContext())
    assert "Dify SaaS Support Agent Core Router" in out
    assert "读取成功后再调用 `classify_ticket`" in out


def test_base_support_skill_is_static_and_cacheable():
    out = _build(PromptContext())

    assert "## Skill: dify_support_ticket" in out
    assert "结构化证据台账" in out
    assert "fast / standard / deep / high_risk" in out


def test_reply_templates_NOT_in_system_prompt():
    out = _build(PromptContext())
    assert "客户回复模板" not in out
    assert "Missing Critical Info" not in out


# ─────────── SQL/DB/REFUND bulk context stays out of prompts ───────────
# 分类只返回当前类别策略；表结构由调查时的只读 introspection 获取。

def test_sql_templates_NOT_in_system_prompt():
    """SQL_TEMPLATES 不出现在 system prompt。"""
    out = _build(PromptContext(ticket_type="workflow"))
    out2 = _build(PromptContext())
    # SQL_TEMPLATES.md 里有非常具体的 marker；用它判断是否真的在 system prompt
    # 里。"SELECT" 太宽泛，COMMON_PLAYBOOK / DEV_REPORT 里也可能出现。
    assert "## SQL 查询模板" not in out
    assert "## SQL 查询模板" not in out2


def test_refund_flow_NOT_in_system_prompt():
    """完整 REFUND_FLOW 不出现在 system prompt。"""
    out_refund = _build(PromptContext(ticket_type="refund"))
    out_other = _build(PromptContext(ticket_type="workflow"))
    out_blank = _build(PromptContext())
    assert "退款工单处理流程" not in out_refund
    assert "退款工单处理流程" not in out_other
    assert "退款工单处理流程" not in out_blank


def test_classify_ticket_attaches_only_category_strategy():
    """分类只注入当前类别策略，不重复基础 Skill 或整库 schema/template。"""
    import asyncio
    from zd_agent.tools.classify import ClassifyTicket

    tool = ClassifyTicket()
    session = _ticket_session()
    result = asyncio.get_event_loop().run_until_complete(
        tool.execute({"ticket_type": "workflow", "reasoning": "test"}, session)
    )
    assert result.success is True
    assert "类型策略 Skill: workflow" in (result.output or "")
    assert "工作流问题处理策略" in (result.output or "")
    assert "## Skill: dify_support_ticket" not in (result.output or "")
    assert "## SQL 查询模板（lazy-loaded）" not in (result.output or "")
    assert "## 生产库 Schema 速查" not in (result.output or "")


def test_custom_skill_context_loaded_from_admin_config(monkeypatch):
    from zd_agent import config as config_mod
    from zd_agent.config import AppConfig, CustomSkillConfig
    from zd_agent.runtime.prompt_loader import build_static_system_prompt

    cfg = AppConfig()
    cfg.skills.custom = [
        CustomSkillConfig(
            name="billing_refund_skill",
            description="Refund SOP",
            content="Always verify requester is workspace owner before refund checks.",
            enabled=True,
        ),
    ]
    cfg.skills.enabled = ["dify_support_ticket", "billing_refund_skill"]
    monkeypatch.setattr(config_mod, "_config", cfg)

    out = build_static_system_prompt()

    assert "billing_refund_skill" in out
    assert "Refund SOP" in out
    assert "Always verify requester is workspace owner before refund checks." in out


def test_classify_ticket_keeps_scoped_billing_strategy():
    """类别策略保留相关表路由，但不注入整库 SQL 模板。"""
    import asyncio
    from zd_agent.tools.classify import ClassifyTicket

    tool = ClassifyTicket()
    session = _ticket_session()
    result = asyncio.get_event_loop().run_until_complete(
        tool.execute({"ticket_type": "billing", "reasoning": "test"}, session)
    )

    assert result.success is True
    assert "计费" in (result.output or "")
    assert "SQL 查询模板（lazy-loaded）" not in (result.output or "")


def test_classify_ticket_skips_sql_for_non_sql_categories():
    """ticket_type ∉ NEEDS_SQL → classify tool 输出不附加 SQL_TEMPLATES。"""
    import asyncio
    from zd_agent.tools.classify import ClassifyTicket

    tool = ClassifyTicket()
    session = _ticket_session()
    result = asyncio.get_event_loop().run_until_complete(
        tool.execute({"ticket_type": "product_usage", "reasoning": "test"}, session)
    )
    assert result.success is True
    assert "类型策略 Skill: product_usage" in (result.output or "")
    assert "SQL 查询模板" not in (result.output or "")
    assert "退款流程" not in (result.output or "")


def test_classify_ticket_attaches_refund_only_for_refund():
    """REFUND_FLOW 只在 refund 工单出现。"""
    import asyncio
    from zd_agent.tools.classify import ClassifyTicket

    tool = ClassifyTicket()
    session = _ticket_session()
    refund_out = asyncio.get_event_loop().run_until_complete(
        tool.execute({"ticket_type": "refund", "reasoning": "test"}, session)
    )
    workflow_out = asyncio.get_event_loop().run_until_complete(
        tool.execute({"ticket_type": "workflow", "reasoning": "test"}, session)
    )
    assert "退款工单处理策略" in (refund_out.output or "")
    assert "退款工单处理流程" not in (refund_out.output or "")
    assert "退款流程" not in (workflow_out.output or "")


def test_classify_requires_fresh_zendesk_context():
    import asyncio

    from zd_agent.tools.classify import ClassifyTicket

    result = asyncio.get_event_loop().run_until_complete(
        ClassifyTicket().execute(
            {"ticket_type": "workflow", "reasoning": "test"},
            AgentSession(ticket_id=1),
        )
    )

    assert result.success is False
    assert "zendesk_get_ticket" in (result.error or "")


def test_repeated_same_classification_is_idempotent():
    import asyncio

    from zd_agent.tools.classify import ClassifyTicket

    tool = ClassifyTicket()
    session = _ticket_session()
    first = asyncio.get_event_loop().run_until_complete(
        tool.execute({"ticket_type": "workflow", "reasoning": "first"}, session)
    )
    second = asyncio.get_event_loop().run_until_complete(
        tool.execute({"ticket_type": "workflow", "reasoning": "repeat"}, session)
    )

    assert first.success is True
    assert "工作流问题处理策略" in (first.output or "")
    assert second.success is True
    assert "已注入" in (second.output or "")
    assert "工作流问题处理策略" not in (second.output or "")


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


def test_dev_report_NOT_in_system_prompt_for_platform_issue():
    out = _build(PromptContext(ticket_type="platform_issue"))
    assert "开发转交格式" not in out
    assert "Dev Report" not in out


def test_dev_report_NOT_in_system_prompt_regardless_of_category():
    out = _build(PromptContext(ticket_type="refund"))
    assert "开发转交格式" not in out
    assert "Dev Report" not in out
    out_blank = _build(PromptContext())
    assert "开发转交格式" not in out_blank
    assert "Dev Report" not in out_blank


# ─────────── DB schema is queried on demand, never bulk-injected ───────────

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


def test_classify_ticket_does_not_attach_db_schema(tmp_path, monkeypatch):
    """即使 schema 文件存在，分类结果也不再整段注入。"""
    import asyncio
    from zd_agent.runtime.prompt import defaults as defaults_mod
    from zd_agent.tools.classify import ClassifyTicket

    fake = (
        "# Dify SaaS Database Schema\n\n"
        "## langgenius\n\n**用途**：Dify SaaS 主业务库 (mocked)\n"
    )
    schema_file = tmp_path / "schema.md"
    schema_file.write_text(fake, encoding="utf-8")
    monkeypatch.setattr(defaults_mod, "_db_schema_path", lambda: schema_file)

    tool = ClassifyTicket()
    session = _ticket_session()
    result = asyncio.get_event_loop().run_until_complete(
        tool.execute({"ticket_type": "workflow", "reasoning": "test"}, session)
    )
    assert "Dify SaaS 主业务库 (mocked)" not in (result.output or "")


def test_classify_ticket_skips_db_schema_when_file_missing(tmp_path, monkeypatch):
    """schema 文件不存在时分类结果仍保持精简。"""
    import asyncio
    from zd_agent.runtime.prompt import defaults as defaults_mod
    from zd_agent.tools.classify import ClassifyTicket

    monkeypatch.setattr(defaults_mod, "_db_schema_path", lambda: tmp_path / "nope.md")

    tool = ClassifyTicket()
    session = _ticket_session()
    result = asyncio.get_event_loop().run_until_complete(
        tool.execute({"ticket_type": "workflow", "reasoning": "test"}, session)
    )
    assert "SQL 查询模板" not in (result.output or "")
    assert "生产库 Schema 速查" not in (result.output or "")
