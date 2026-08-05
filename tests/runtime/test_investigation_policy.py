from __future__ import annotations

from zd_agent.runtime.investigation import (
    allowed_tool_names,
    current_investigation_phase,
    evaluate_tool_call,
)
from zd_agent.session.agent_session import AgentSession, TicketContext


def _planned_session(
    *,
    ticket_type: str,
    route: str,
    independent_lanes: list[str] | None = None,
) -> AgentSession:
    session = AgentSession(ticket_id=3798)
    session.investigation_policy_enabled = True
    session.context = TicketContext(ticket_id=3798)
    session.ticket_type = ticket_type
    session.investigation_plan = {
        "route": route,
        "customer_question": "Why did this request fail?",
        "environment": "saas",
        "object_identifiers": ["ticket=3798"],
        "time_window": "2026-08-06T01:00:00Z/2026-08-06T01:10:00Z",
        "execution_chain": ["customer", "api", "runtime"],
        "evidence_lanes": ["customer_object"],
        "independent_lanes": independent_lanes or [],
        "unresolved_facts": ["root cause"],
    }
    session.investigation_route = route
    return session


def test_phase_progression_requires_intake_classification_and_plan() -> None:
    session = AgentSession(ticket_id=3798)
    session.investigation_policy_enabled = True

    assert current_investigation_phase(session) == "intake"

    session.context = TicketContext(ticket_id=3798)
    assert current_investigation_phase(session) == "classify"

    session.ticket_type = "workflow"
    assert current_investigation_phase(session) == "plan"

    session.investigation_plan = {"route": "standard"}
    session.investigation_route = "standard"
    assert current_investigation_phase(session) == "investigate"

    session.reply_sent = True
    assert current_investigation_phase(session) == "complete"


def test_intake_exposes_only_current_ticket_read() -> None:
    session = AgentSession(ticket_id=3798)
    session.investigation_policy_enabled = True

    allowed = allowed_tool_names(
        session,
        {
            "zendesk_get_ticket",
            "classify_ticket",
            "sql_query",
            "signoz_search_logs",
            "zendesk_public_reply",
        },
    )

    assert allowed == {"zendesk_get_ticket"}


def test_fast_product_route_exposes_docs_but_not_live_runtime_or_subagents() -> None:
    session = _planned_session(ticket_type="product_usage", route="fast")

    allowed = allowed_tool_names(
        session,
        {
            "official_docs_search",
            "official_docs_read",
            "sql_query",
            "inspector_exec",
            "signoz_search_logs",
            "run_subagent",
            "run_subagents_parallel",
            "zendesk_public_reply",
        },
    )

    assert "official_docs_search" in allowed
    assert "official_docs_read" in allowed
    assert "zendesk_public_reply" in allowed
    assert "sql_query" not in allowed
    assert "inspector_exec" not in allowed
    assert "signoz_search_logs" not in allowed
    assert "run_subagent" not in allowed
    assert "run_subagents_parallel" not in allowed


def test_high_risk_billing_exposes_identity_and_billing_tools_without_subagents() -> None:
    session = _planned_session(ticket_type="billing", route="high_risk")

    allowed = allowed_tool_names(
        session,
        {
            "inspector_exec",
            "sql_query",
            "official_docs_search",
            "signoz_search_logs",
            "run_subagents_parallel",
            "zendesk_public_reply",
        },
    )

    assert {"inspector_exec", "sql_query", "zendesk_public_reply"} <= allowed
    assert "signoz_search_logs" not in allowed
    assert "run_subagents_parallel" not in allowed


def test_high_risk_billing_quota_lane_does_not_open_runtime_or_source_tools() -> None:
    session = _planned_session(ticket_type="billing", route="high_risk")
    session.investigation_plan["evidence_lanes"] = [
        "Tenant identity lookup by form email",
        "Authoritative billing record for the resolved tenant",
        "Quota/usage record for the resolved tenant",
    ]

    allowed = allowed_tool_names(
        session,
        {
            "inspector_exec",
            "sql_query",
            "signoz_search_logs",
            "git_grep",
            "github_search_prs",
            "zendesk_public_reply",
        },
    )

    assert {"inspector_exec", "sql_query", "zendesk_public_reply"} <= allowed
    assert "signoz_search_logs" not in allowed
    assert "git_grep" not in allowed
    assert "github_search_prs" not in allowed


def test_high_risk_route_opens_runtime_and_source_only_when_explicitly_planned() -> None:
    session = _planned_session(ticket_type="billing", route="high_risk")
    session.investigation_plan["evidence_lanes"] = [
        "runtime logs for the exact billing request",
        "source implementation for the deployed version",
    ]

    allowed = allowed_tool_names(
        session,
        {
            "inspector_exec",
            "signoz_search_logs",
            "git_grep",
            "run_subagents_parallel",
        },
    )

    assert "inspector_exec" in allowed
    assert "signoz_search_logs" in allowed
    assert "git_grep" in allowed
    assert "run_subagents_parallel" not in allowed


def test_deep_route_requires_two_independent_lanes_before_parallel_subagents() -> None:
    without_lanes = _planned_session(
        ticket_type="platform_issue",
        route="deep",
        independent_lanes=["runtime"],
    )
    with_lanes = _planned_session(
        ticket_type="platform_issue",
        route="deep",
        independent_lanes=["runtime", "source"],
    )

    assert not evaluate_tool_call(
        session=without_lanes,
        tool_name="run_subagents_parallel",
    ).allowed
    assert evaluate_tool_call(
        session=with_lanes,
        tool_name="run_subagents_parallel",
    ).allowed


def test_execution_policy_rejects_unexposed_tool_even_if_registered() -> None:
    session = AgentSession(ticket_id=3798)
    session.investigation_policy_enabled = True

    decision = evaluate_tool_call(session=session, tool_name="sql_query")

    assert decision.allowed is False
    assert "intake" in decision.reason
