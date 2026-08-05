from __future__ import annotations

from zd_agent.runtime.reply_evidence import (
    body_asserts_root_cause,
    render_evidence_ledger,
    validate_public_reply_evidence,
)


def _success(
    tool: str,
    result: str = "confirmed live result",
    *,
    call_id: str | None = None,
) -> dict:
    return {
        "call_id": call_id or f"call-{tool}",
        "round": 1,
        "tool": tool,
        "args": {},
        "success": True,
        "error": None,
        "result_preview": result,
    }


def _root_cause_ledger(*, route: str = "standard") -> dict:
    return {
        "route": route,
        "decision_scope": "technical_diagnosis",
        "root_cause_status": "confirmed",
        "root_cause_summary": "A stale provider credential caused authentication failure.",
        "rows": [
            {
                "claim": "The provider rejected the stale credential.",
                "causal_role": "root_cause",
                "status": "confirmed",
                "source_tools": ["signoz_search_logs", "sql_query"],
                "source_call_ids": [
                    "call-signoz_search_logs",
                    "call-sql_query",
                ],
                "object_identifiers": "workspace=w1, provider=p1",
                "time_window": "2026-07-29T01:00:00Z/2026-07-29T01:10:00Z",
                "observed_result": "Matching auth rejection and stale credential version.",
                "alternatives_checked": "Rate limit and provider outage were ruled out.",
            },
            {
                "claim": "The provider boundary rejected the request.",
                "causal_role": "failing_boundary",
                "status": "confirmed",
                "source_tools": ["signoz_search_logs", "sql_query"],
                "source_call_ids": [
                    "call-signoz_search_logs",
                    "call-sql_query",
                ],
                "object_identifiers": "workspace=w1, provider=p1",
                "time_window": "2026-07-29T01:00:00Z/2026-07-29T01:10:00Z",
                "observed_result": "The provider returned an authentication rejection.",
                "alternatives_checked": "The request reached the provider boundary.",
            },
        ],
    }


def _fast_ledger() -> dict:
    return {
        "route": "fast",
        "decision_scope": "customer_guidance",
        "root_cause_status": "not_applicable",
        "root_cause_summary": "",
        "rows": [
            {
                "claim": "The documented API uses POST /v1/files/upload.",
                "causal_role": "product_fact",
                "status": "confirmed",
                "source_tools": ["official_docs_read"],
                "source_call_ids": ["call-official_docs_read"],
                "object_identifiers": "Dify Cloud API",
                "time_window": "not_applicable",
                "observed_result": "The current API reference documents the endpoint.",
                "alternatives_checked": "Checked the current API reference.",
            },
        ],
    }


def test_detects_customer_visible_root_cause_assertions() -> None:
    assert body_asserts_root_cause(
        "We confirmed that the outage was caused by a database deadlock."
    )
    assert body_asserts_root_cause("The issue was due to an expired credential.")
    assert not body_asserts_root_cause(
        "We are still investigating the root cause and will follow up."
    )


def test_solution_requires_internal_analysis_and_ledger() -> None:
    blockers = validate_public_reply_evidence(
        reply_type="solution",
        body="Please retry the request.",
        internal_analysis="",
        evidence_ledger=None,
        tool_call_log=[],
        ticket_type="workflow",
    )

    assert any("internal_analysis" in blocker for blocker in blockers)
    assert any("evidence_ledger" in blocker for blocker in blockers)


def test_standard_solution_requires_confirmed_root_cause() -> None:
    ledger = _root_cause_ledger()
    ledger["root_cause_status"] = "unknown"
    ledger["rows"][0]["status"] = "unknown"

    blockers = validate_public_reply_evidence(
        reply_type="solution",
        body="Please retry the request.",
        internal_analysis="Checked the current ticket and runtime evidence.",
        evidence_ledger=ledger,
        tool_call_log=[_success("signoz_search_logs"), _success("sql_query")],
        ticket_type="workflow",
    )

    assert any("confirmed root cause" in blocker for blocker in blockers)


def test_successful_tool_evidence_allows_confirmed_solution() -> None:
    blockers = validate_public_reply_evidence(
        reply_type="solution",
        body="The stale provider credential caused the authentication failure.",
        internal_analysis="Checked the exact workspace, provider, and incident window.",
        evidence_ledger=_root_cause_ledger(),
        tool_call_log=[_success("signoz_search_logs"), _success("sql_query")],
        ticket_type="workflow",
    )

    assert blockers == []


def test_failed_tools_cannot_satisfy_ledger_sources() -> None:
    blockers = validate_public_reply_evidence(
        reply_type="solution",
        body="The stale provider credential caused the authentication failure.",
        internal_analysis="Checked the exact workspace, provider, and incident window.",
        evidence_ledger=_root_cause_ledger(),
        tool_call_log=[
            {
                "round": 1,
                "tool": "signoz_search_logs",
                "args": {"searchText": "credential"},
                "success": False,
                "error": "timeout",
                "result_preview": "credential lookup timed out",
            },
            _success("sql_query"),
        ],
        ticket_type="workflow",
    )

    assert any("signoz_search_logs" in blocker for blocker in blockers)


def test_historical_ticket_cannot_prove_current_root_cause() -> None:
    ledger = _root_cause_ledger()
    ledger["rows"][0]["source_tools"] = ["ticket_record_read"]
    ledger["rows"][0]["source_call_ids"] = ["call-ticket_record_read"]

    blockers = validate_public_reply_evidence(
        reply_type="solution",
        body="The stale provider credential caused the authentication failure.",
        internal_analysis="Compared the current report with an older ticket.",
        evidence_ledger=ledger,
        tool_call_log=[_success("ticket_record_read")],
        ticket_type="workflow",
    )

    assert any("historical" in blocker for blocker in blockers)


def test_fast_authoritative_product_fact_does_not_require_root_cause() -> None:
    blockers = validate_public_reply_evidence(
        reply_type="solution",
        body="Use POST /v1/files/upload for this request.",
        internal_analysis="Verified the current Dify Cloud API reference.",
        evidence_ledger=_fast_ledger(),
        tool_call_log=[_success("official_docs_read")],
        ticket_type="api",
    )

    assert blockers == []


def test_ask_and_handoff_allow_unknown_root_cause_without_ledger() -> None:
    for reply_type in ("ask", "handoff"):
        blockers = validate_public_reply_evidence(
            reply_type=reply_type,
            body="Please share the workflow run ID so we can continue checking.",
            internal_analysis="The failing execution is not identified yet.",
            evidence_ledger=None,
            tool_call_log=[],
            ticket_type="workflow",
        )
        assert blockers == []


def test_causal_claim_cannot_bypass_gate_via_handoff() -> None:
    blockers = validate_public_reply_evidence(
        reply_type="handoff",
        body="The outage was caused by a database deadlock. A human will follow up.",
        internal_analysis="The exact root cause has not been verified.",
        evidence_ledger=None,
        tool_call_log=[],
        ticket_type="platform_issue",
    )

    assert any("evidence_ledger" in blocker for blocker in blockers)


def test_high_risk_technical_fact_can_be_replied_without_business_decision() -> None:
    ledger = {
        "route": "high_risk",
        "decision_scope": "technical_fact_only",
        "root_cause_status": "not_applicable",
        "root_cause_summary": "",
        "rows": [
            {
                "claim": "The subscription is currently canceled.",
                "causal_role": "current_state",
                "status": "confirmed",
                "source_tools": ["sql_query"],
                "source_call_ids": ["call-sql_query"],
                "object_identifiers": "workspace=w1, subscription=s1",
                "time_window": "checked_at=2026-07-29T02:00:00Z",
                "observed_result": "Subscription status is canceled.",
                "alternatives_checked": "Checked the active subscription record.",
            },
        ],
    }

    blockers = validate_public_reply_evidence(
        reply_type="solution",
        body="We confirmed that your subscription is currently canceled.",
        internal_analysis="Verified the requester and current subscription record.",
        evidence_ledger=ledger,
        tool_call_log=[_success("sql_query")],
        ticket_type="refund",
    )

    assert blockers == []


def test_high_risk_business_decision_is_blocked() -> None:
    ledger = _fast_ledger()
    ledger["route"] = "high_risk"
    ledger["decision_scope"] = "business_decision"

    blockers = validate_public_reply_evidence(
        reply_type="solution",
        body="Your refund has been approved.",
        internal_analysis="Requester identity was checked.",
        evidence_ledger=ledger,
        tool_call_log=[_success("official_docs_read")],
        ticket_type="refund",
    )

    assert any("business decision" in blocker for blocker in blockers)


def test_rendered_ledger_preserves_auditable_fields() -> None:
    rendered = render_evidence_ledger(_root_cause_ledger())

    assert "route: standard" in rendered
    assert "causal_role: root_cause" in rendered
    assert "signoz_search_logs" in rendered
    assert "workspace=w1" in rendered


def test_material_row_cannot_be_proved_by_tool_name_only() -> None:
    ledger = _root_cause_ledger()
    ledger["rows"][0].pop("source_call_ids")

    blockers = validate_public_reply_evidence(
        reply_type="solution",
        body="The stale provider credential caused the authentication failure.",
        internal_analysis="Checked the exact workspace and incident window.",
        evidence_ledger=ledger,
        tool_call_log=[_success("signoz_search_logs"), _success("sql_query")],
        ticket_type="workflow",
        planned_route="standard",
    )

    assert any("source_call_ids" in blocker for blocker in blockers)


def test_material_row_accepts_exact_successful_call_ids() -> None:
    ledger = _root_cause_ledger()
    for row in ledger["rows"]:
        row["source_call_ids"] = ["logs-1", "db-1"]

    blockers = validate_public_reply_evidence(
        reply_type="solution",
        body="The stale provider credential caused the authentication failure.",
        internal_analysis="Checked the exact workspace and incident window.",
        evidence_ledger=ledger,
        tool_call_log=[
            _success("signoz_search_logs", call_id="logs-1"),
            _success("sql_query", call_id="db-1"),
        ],
        ticket_type="workflow",
        planned_route="standard",
    )

    assert blockers == []


def test_unknown_or_failed_source_call_id_is_blocked() -> None:
    ledger = _root_cause_ledger()
    ledger["rows"][0]["source_call_ids"] = ["missing-call"]

    blockers = validate_public_reply_evidence(
        reply_type="solution",
        body="The stale provider credential caused the authentication failure.",
        internal_analysis="Checked the exact workspace and incident window.",
        evidence_ledger=ledger,
        tool_call_log=[_success("signoz_search_logs", call_id="logs-1")],
        ticket_type="workflow",
        planned_route="standard",
    )

    assert any("missing-call" in blocker for blocker in blockers)


def test_solution_route_must_match_frozen_investigation_plan() -> None:
    ledger = _root_cause_ledger(route="deep")
    ledger["rows"][0]["source_call_ids"] = ["logs-1", "db-1"]

    blockers = validate_public_reply_evidence(
        reply_type="solution",
        body="The stale provider credential caused the authentication failure.",
        internal_analysis="Checked the exact workspace and incident window.",
        evidence_ledger=ledger,
        tool_call_log=[
            _success("signoz_search_logs", call_id="logs-1"),
            _success("sql_query", call_id="db-1"),
        ],
        ticket_type="workflow",
        planned_route="standard",
    )

    assert any("planned route" in blocker.lower() for blocker in blockers)


def test_deep_solution_requires_failing_boundary_and_current_state() -> None:
    ledger = _root_cause_ledger(route="deep")
    ledger["rows"] = [
        row for row in ledger["rows"] if row["causal_role"] != "failing_boundary"
    ]
    ledger["rows"][0]["source_call_ids"] = ["logs-1", "db-1"]

    blockers = validate_public_reply_evidence(
        reply_type="solution",
        body="The stale provider credential caused the authentication failure.",
        internal_analysis="Checked the exact workspace and incident window.",
        evidence_ledger=ledger,
        tool_call_log=[
            _success("signoz_search_logs", call_id="logs-1"),
            _success("sql_query", call_id="db-1"),
        ],
        ticket_type="workflow",
        planned_route="deep",
        investigation_plan={
            "route": "deep",
            "evidence_lanes": ["customer_object", "impact_scope", "baseline"],
        },
    )

    assert any("failing_boundary" in blocker for blocker in blockers)
    assert any("current_state" in blocker for blocker in blockers)


def test_deep_solution_requires_impact_scope_and_baseline_plan_lanes() -> None:
    ledger = _root_cause_ledger(route="deep")
    ledger["rows"].extend(
        [
            {
                **ledger["rows"][0],
                "claim": "The provider boundary rejected the request.",
                "causal_role": "failing_boundary",
            },
            {
                **ledger["rows"][0],
                "claim": "A matching request now succeeds.",
                "causal_role": "current_state",
            },
        ]
    )

    blockers = validate_public_reply_evidence(
        reply_type="solution",
        body="The stale provider credential caused the authentication failure.",
        internal_analysis="Checked the exact workspace and incident window.",
        evidence_ledger=ledger,
        tool_call_log=[
            _success("signoz_search_logs"),
            _success("sql_query"),
        ],
        ticket_type="workflow",
        planned_route="deep",
        investigation_plan={"route": "deep", "evidence_lanes": ["customer_object"]},
    )

    assert any("impact_scope" in blocker for blocker in blockers)
    assert any("baseline" in blocker for blocker in blockers)
