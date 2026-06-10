"""Test SaaS classify categories."""
import pytest

from zd_agent.tools.classify import ClassifyTicket, VALID_TYPES


@pytest.mark.asyncio
async def test_all_new_saas_types_valid(fake_session):
    tool = ClassifyTicket()
    saas_types = [
        "platform_issue", "knowledge_base", "workflow", "api",
        "permissions", "product_usage", "billing", "refund",
        "insufficient_info",
    ]
    for t in saas_types:
        assert t in VALID_TYPES, f"{t} missing from VALID_TYPES"


def test_legacy_mention_and_general_retained():
    assert "mention" in VALID_TYPES
    assert "general" in VALID_TYPES


def test_legacy_enterprise_types_removed():
    # Enterprise-specific types should no longer be valid
    assert "config-deploy" not in VALID_TYPES
    assert "code-bug" not in VALID_TYPES
    assert "usage-howto" not in VALID_TYPES


@pytest.mark.asyncio
async def test_classify_refund_loads_strategy(fake_session):
    tool = ClassifyTicket()
    fake_session.ticket_type = ""
    fake_session.classification_reasoning = ""
    r = await tool.execute(
        {"ticket_type": "refund", "reasoning": "客户要求退款"},
        fake_session,
    )
    assert r.success is True
    assert "refund" in r.output.lower() or "退款" in r.output
    assert fake_session.ticket_type == "refund"
    # refund strategy content should be included
    assert "退款工单处理策略" in r.output
    assert "ticket_escalate" in r.output  # 飞书推财务这一步必出现


@pytest.mark.asyncio
async def test_classify_invalid_type_falls_back(fake_session):
    tool = ClassifyTicket()
    fake_session.ticket_type = ""
    r = await tool.execute(
        {"ticket_type": "bogus_type", "reasoning": "x"},
        fake_session,
    )
    assert r.success is True
    assert fake_session.ticket_type == "general"


@pytest.mark.asyncio
async def test_classify_normalizes_ticket_type(fake_session):
    tool = ClassifyTicket()
    fake_session.ticket_type = ""

    r = await tool.execute(
        {"ticket_type": " Refund ", "reasoning": "客户要求退款"},
        fake_session,
    )

    assert r.success is True
    assert fake_session.ticket_type == "refund"
    assert "工单已分类为 **refund**" in r.output


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("args", "expected_error"),
    [
        (
            {"ticket_type": ["workflow"], "reasoning": "x"},
            "ticket_type must be a string",
        ),
        (
            {"ticket_type": "workflow", "reasoning": {"why": "x"}},
            "reasoning must be a string",
        ),
        (
            {"ticket_type": "workflow", "reasoning": "   "},
            "reasoning is empty",
        ),
    ],
)
async def test_classify_rejects_invalid_argument_types(
    fake_session, args, expected_error,
):
    tool = ClassifyTicket()
    fake_session.ticket_type = ""
    fake_session.classification_reasoning = ""

    r = await tool.execute(args, fake_session)

    assert r.success is False
    assert r.error == expected_error
    assert fake_session.ticket_type == ""
    assert fake_session.classification_reasoning == ""


@pytest.mark.asyncio
async def test_all_saas_strategy_files_load(fake_session):
    tool = ClassifyTicket()
    saas_types = [
        "platform_issue", "knowledge_base", "workflow", "api",
        "permissions", "product_usage", "billing", "refund",
        "insufficient_info",
    ]
    for t in saas_types:
        fake_session.ticket_type = ""
        r = await tool.execute(
            {"ticket_type": t, "reasoning": "test"},
            fake_session,
        )
        # Each strategy file must load successfully (non-empty output beyond header)
        assert r.success is True, f"{t} failed"
        assert "策略" in r.output or "步骤" in r.output, f"{t} strategy file empty"
