"""Structured evidence gate for irreversible customer-visible replies."""

from __future__ import annotations

import re
from typing import Any, Final

_VALID_ROUTES: Final = {"fast", "standard", "deep", "high_risk"}
_VALID_DECISION_SCOPES: Final = {
    "customer_guidance",
    "technical_diagnosis",
    "technical_fact_only",
    "business_decision",
}
_VALID_ROOT_CAUSE_STATUSES: Final = {
    "confirmed",
    "evidence_supported",
    "unknown",
    "not_applicable",
}
_VALID_CAUSAL_ROLES: Final = {
    "symptom",
    "failing_boundary",
    "immediate_trigger",
    "root_cause",
    "contributing_factor",
    "recovery_event",
    "current_state",
    "product_fact",
}
_VALID_FINDING_STATUSES: Final = {
    "confirmed",
    "evidence_supported",
    "ruled_out",
    "unknown",
}

_ROOT_CAUSE_ASSERTION_PATTERNS: Final = (
    re.compile(r"\b(?:is|are|was|were)\s+caused\s+by\b", re.IGNORECASE),
    re.compile(r"\b(?:is|are|was|were)\s+due\s+to\b", re.IGNORECASE),
    re.compile(r"\bresult(?:ed|s)?\s+from\b", re.IGNORECASE),
    re.compile(r"\broot\s+cause\s+(?:is|was|has\s+been)\b", re.IGNORECASE),
    re.compile(
        r"\bwe\s+(?:have\s+)?confirmed(?:\s+that)?.{0,120}"
        r"\b(?:caused|triggered|resulted)\b",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        r"\bcaused\s+(?:the|this|your|an?|these|those)\b",
        re.IGNORECASE,
    ),
)

_HISTORICAL_TOOL_PREFIXES: Final = ("ticket_record_",)
_NON_EVIDENCE_TOOLS: Final = {
    "archive_ticket",
    "classify_ticket",
    "feishu_push",
    "run_subagent",
    "run_subagents_parallel",
    "zendesk_public_reply",
}


def body_asserts_root_cause(body: str) -> bool:
    """Return whether customer prose asserts a causal conclusion."""
    text = str(body or "")
    return any(pattern.search(text) for pattern in _ROOT_CAUSE_ASSERTION_PATTERNS)


def _clean_text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _successful_evidence_tools(
    tool_call_log: list[dict[str, Any]],
) -> set[str]:
    names: set[str] = set()
    for item in tool_call_log or []:
        if not isinstance(item, dict) or item.get("success") is not True:
            continue
        name = _clean_text(item.get("tool"))
        result = _clean_text(item.get("result_preview"))
        if name and result and name not in _NON_EVIDENCE_TOOLS:
            names.add(name)
        metadata = item.get("metadata")
        if not isinstance(metadata, dict):
            continue
        nested = metadata.get("evidence_tool_calls")
        if not isinstance(nested, list):
            continue
        for child in nested:
            if not isinstance(child, dict) or child.get("success") is not True:
                continue
            child_name = _clean_text(child.get("tool"))
            child_result = _clean_text(child.get("result_preview"))
            if (
                child_name
                and child_result
                and child_name not in _NON_EVIDENCE_TOOLS
            ):
                names.add(child_name)
    return names


def _is_historical_tool(name: str) -> bool:
    return name.startswith(_HISTORICAL_TOOL_PREFIXES)


def _validate_row(
    *,
    row: Any,
    index: int,
    route: str,
    successful_tools: set[str],
) -> tuple[list[str], dict[str, Any] | None]:
    label = f"evidence_ledger.rows[{index}]"
    if not isinstance(row, dict):
        return [f"{label} must be an object."], None

    blockers: list[str] = []
    claim = _clean_text(row.get("claim"))
    role = _clean_text(row.get("causal_role"))
    status = _clean_text(row.get("status"))
    object_identifiers = _clean_text(row.get("object_identifiers"))
    time_window = _clean_text(row.get("time_window"))
    observed_result = _clean_text(row.get("observed_result"))
    alternatives_checked = _clean_text(row.get("alternatives_checked"))
    raw_source_tools = row.get("source_tools")

    if not claim:
        blockers.append(f"{label}.claim is required.")
    if role not in _VALID_CAUSAL_ROLES:
        blockers.append(
            f"{label}.causal_role must be one of "
            f"{sorted(_VALID_CAUSAL_ROLES)}."
        )
    if status not in _VALID_FINDING_STATUSES:
        blockers.append(
            f"{label}.status must be one of "
            f"{sorted(_VALID_FINDING_STATUSES)}."
        )
    if not isinstance(raw_source_tools, list) or not all(
        isinstance(tool, str) and tool.strip() for tool in raw_source_tools
    ):
        source_tools: list[str] = []
        blockers.append(f"{label}.source_tools must be a non-empty string list.")
    else:
        source_tools = list(dict.fromkeys(tool.strip() for tool in raw_source_tools))
        if not source_tools:
            blockers.append(f"{label}.source_tools must not be empty.")

    material_status = status in {"confirmed", "evidence_supported", "ruled_out"}
    if material_status:
        if not object_identifiers:
            blockers.append(f"{label}.object_identifiers is required.")
        if not time_window:
            blockers.append(f"{label}.time_window is required.")
        if not observed_result:
            blockers.append(f"{label}.observed_result is required.")
        if not alternatives_checked:
            blockers.append(f"{label}.alternatives_checked is required.")
        for source_tool in source_tools:
            if _is_historical_tool(source_tool):
                blockers.append(
                    f"{label} cites historical tool {source_tool}; historical "
                    "tickets may guide the next check but cannot prove the "
                    "current ticket."
                )
            elif source_tool not in successful_tools:
                blockers.append(
                    f"{label} cites {source_tool}, but no successful tool result "
                    "from that source exists in this session."
                )

    normalized = {
        "claim": claim,
        "causal_role": role,
        "status": status,
        "source_tools": source_tools,
        "object_identifiers": object_identifiers,
        "time_window": time_window,
        "observed_result": observed_result,
        "alternatives_checked": alternatives_checked,
    }
    return blockers, normalized


def validate_public_reply_evidence(
    *,
    reply_type: str,
    body: str,
    internal_analysis: str,
    evidence_ledger: Any,
    tool_call_log: list[dict[str, Any]],
    ticket_type: str = "",
) -> list[str]:
    """Validate the evidence contract for one model-authored public reply.

    ``ask`` and ``handoff`` remain available when the root cause is unknown.
    A ``solution`` must instead be one of:

    - authoritative fast-route product guidance;
    - a standard/deep technical diagnosis with a confirmed root cause; or
    - high-risk technical facts only, without making the business decision.
    """
    blockers: list[str] = []
    if not _clean_text(internal_analysis):
        blockers.append(
            "internal_analysis is required for every model-authored public reply."
        )

    causal_assertion = body_asserts_root_cause(body)
    ledger_required = reply_type == "solution" or causal_assertion
    if not ledger_required and evidence_ledger in (None, {}):
        return blockers
    if not isinstance(evidence_ledger, dict):
        blockers.append(
            "evidence_ledger is required for solution replies and causal claims."
        )
        return blockers

    route = _clean_text(evidence_ledger.get("route"))
    decision_scope = _clean_text(evidence_ledger.get("decision_scope"))
    root_cause_status = _clean_text(evidence_ledger.get("root_cause_status"))
    root_cause_summary = _clean_text(evidence_ledger.get("root_cause_summary"))
    rows = evidence_ledger.get("rows")

    if route not in _VALID_ROUTES:
        blockers.append(
            f"evidence_ledger.route must be one of {sorted(_VALID_ROUTES)}."
        )
    if decision_scope not in _VALID_DECISION_SCOPES:
        blockers.append(
            "evidence_ledger.decision_scope must be one of "
            f"{sorted(_VALID_DECISION_SCOPES)}."
        )
    if root_cause_status not in _VALID_ROOT_CAUSE_STATUSES:
        blockers.append(
            "evidence_ledger.root_cause_status must be one of "
            f"{sorted(_VALID_ROOT_CAUSE_STATUSES)}."
        )
    if root_cause_status in {"confirmed", "evidence_supported"} and not root_cause_summary:
        blockers.append(
            "evidence_ledger.root_cause_summary is required when a root cause "
            "status is asserted."
        )
    if not isinstance(rows, list) or not rows:
        blockers.append("evidence_ledger.rows must be a non-empty list.")
        return blockers

    successful_tools = _successful_evidence_tools(tool_call_log)
    normalized_rows: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        row_blockers, normalized = _validate_row(
            row=row,
            index=index,
            route=route,
            successful_tools=successful_tools,
        )
        blockers.extend(row_blockers)
        if normalized is not None:
            normalized_rows.append(normalized)

    confirmed_root_causes = [
        row
        for row in normalized_rows
        if row["causal_role"] == "root_cause" and row["status"] == "confirmed"
    ]
    confirmed_fast_facts = [
        row
        for row in normalized_rows
        if row["causal_role"] in {"product_fact", "current_state"}
        and row["status"] == "confirmed"
    ]
    confirmed_high_risk_facts = [
        row
        for row in normalized_rows
        if row["causal_role"]
        in {"product_fact", "current_state", "failing_boundary"}
        and row["status"] == "confirmed"
    ]

    if decision_scope == "business_decision":
        blockers.append(
            "Automated public replies cannot make a high-risk business decision; "
            "reply with verified technical facts and hand off the decision."
        )

    needs_confirmed_root_cause = causal_assertion
    if reply_type == "solution":
        if route == "fast":
            if decision_scope != "customer_guidance":
                blockers.append(
                    "Fast-route solution replies must use "
                    "decision_scope=customer_guidance."
                )
            if not confirmed_fast_facts:
                blockers.append(
                    "Fast-route solution replies require a confirmed product_fact "
                    "or current_state row backed by a successful authoritative tool."
                )
        elif route == "high_risk" and decision_scope == "technical_fact_only":
            if not confirmed_high_risk_facts:
                blockers.append(
                    "High-risk technical-fact replies require a confirmed "
                    "product_fact, current_state, or failing_boundary row."
                )
        else:
            if decision_scope != "technical_diagnosis":
                blockers.append(
                    "Standard/deep diagnosis replies must use "
                    "decision_scope=technical_diagnosis."
                )
            needs_confirmed_root_cause = True

    if needs_confirmed_root_cause:
        if root_cause_status != "confirmed" or not confirmed_root_causes:
            blockers.append(
                "This reply requires a confirmed root cause and a confirmed "
                "root_cause ledger row backed by successful tool evidence."
            )

    return blockers


def render_evidence_ledger(evidence_ledger: dict[str, Any]) -> str:
    """Render validated evidence in the internal note audit trail."""
    lines = [
        "## Evidence Ledger",
        f"- route: {_clean_text(evidence_ledger.get('route'))}",
        f"- decision_scope: {_clean_text(evidence_ledger.get('decision_scope'))}",
        (
            "- root_cause_status: "
            f"{_clean_text(evidence_ledger.get('root_cause_status'))}"
        ),
    ]
    root_summary = _clean_text(evidence_ledger.get("root_cause_summary"))
    if root_summary:
        lines.append(f"- root_cause_summary: {root_summary}")

    for index, row in enumerate(evidence_ledger.get("rows") or [], start=1):
        if not isinstance(row, dict):
            continue
        source_tools = row.get("source_tools")
        source_text = (
            ", ".join(str(tool) for tool in source_tools)
            if isinstance(source_tools, list)
            else ""
        )
        lines.extend(
            [
                "",
                f"### Claim {index}",
                f"- claim: {_clean_text(row.get('claim'))}",
                f"- causal_role: {_clean_text(row.get('causal_role'))}",
                f"- status: {_clean_text(row.get('status'))}",
                f"- source_tools: {source_text}",
                (
                    "- object_identifiers: "
                    f"{_clean_text(row.get('object_identifiers'))}"
                ),
                f"- time_window: {_clean_text(row.get('time_window'))}",
                f"- observed_result: {_clean_text(row.get('observed_result'))}",
                (
                    "- alternatives_checked: "
                    f"{_clean_text(row.get('alternatives_checked'))}"
                ),
            ]
        )
    return "\n".join(lines)
