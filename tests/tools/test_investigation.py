from __future__ import annotations

import pytest

from zd_agent.session.agent_session import AgentSession, TicketContext
from zd_agent.tools.investigation import PlanInvestigation


def _base_args(*, route: str = "standard") -> dict:
    return {
        "route": route,
        "customer_question": "Why can this workspace not add more documents?",
        "environment": "saas",
        "object_identifiers": ["ticket=3798", "tenant=tenant-1"],
        "time_window": "2026-08-06T01:00:00Z/2026-08-06T01:10:00Z",
        "execution_chain": ["customer request", "workspace", "billing", "quota"],
        "evidence_lanes": ["identity", "billing", "quota"],
        "independent_lanes": [],
        "unresolved_facts": ["whether the limit can be changed"],
    }


@pytest.mark.asyncio
async def test_plan_requires_fresh_context_and_classification() -> None:
    session = AgentSession(ticket_id=3798)

    result = await PlanInvestigation().execute(_base_args(), session)

    assert result.success is False
    assert "classification" in (result.error or "").lower()


@pytest.mark.asyncio
async def test_billing_plan_must_use_high_risk_route() -> None:
    session = AgentSession(ticket_id=3798)
    session.context = TicketContext(ticket_id=3798)
    session.ticket_type = "billing"

    result = await PlanInvestigation().execute(_base_args(route="standard"), session)

    assert result.success is False
    assert "high_risk" in (result.error or "")


@pytest.mark.asyncio
async def test_deep_plan_requires_impact_scope_and_baseline_lanes() -> None:
    session = AgentSession(ticket_id=3798)
    session.context = TicketContext(ticket_id=3798)
    session.ticket_type = "platform_issue"
    args = _base_args(route="deep")
    args["independent_lanes"] = ["runtime", "source"]

    result = await PlanInvestigation().execute(args, session)

    assert result.success is False
    assert "impact_scope" in (result.error or "")
    assert "baseline" in (result.error or "")


@pytest.mark.asyncio
async def test_valid_plan_is_frozen_on_session() -> None:
    session = AgentSession(ticket_id=3798)
    session.context = TicketContext(ticket_id=3798)
    session.ticket_type = "billing"
    args = _base_args(route="high_risk")

    result = await PlanInvestigation().execute(args, session)

    assert result.success is True
    assert session.investigation_route == "high_risk"
    assert session.investigation_plan["customer_question"] == args["customer_question"]
    assert session.investigation_plan["object_identifiers"] == args["object_identifiers"]

