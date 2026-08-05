"""Deterministic investigation phases and route-aware tool policy."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Iterable, TypeVar

if TYPE_CHECKING:
    from zd_agent.session.agent_session import AgentSession

_ToolSchemaT = TypeVar("_ToolSchemaT")


VALID_INVESTIGATION_ROUTES = frozenset(
    {"fast", "standard", "deep", "high_risk"}
)

_INTAKE_TOOLS = frozenset({"zendesk_get_ticket"})
_CLASSIFY_TOOLS = frozenset(
    {"zendesk_get_ticket", "zendesk_get_attachments", "classify_ticket"}
)
_PLAN_TOOLS = frozenset(
    {"zendesk_get_ticket", "zendesk_get_attachments", "plan_investigation"}
)

_COMMON_INVESTIGATION_TOOLS = frozenset(
    {
        "zendesk_get_ticket",
        "zendesk_get_attachments",
        "zendesk_search",
        "zendesk_search_articles",
        "zendesk_get_article",
        "zendesk_get_user",
        "zendesk_public_reply",
        "plan_investigation",
        "official_docs_search",
        "official_docs_read",
        "knowledge_search",
        "knowledge_read",
    }
)
_DATABASE_TOOLS = frozenset({"inspector_exec", "sql_query"})
_SOURCE_TOOLS = frozenset(
    {
        "saas_deployed_version",
        "git_show",
        "git_grep",
        "git_log",
        "git_diff",
        "list_repos",
        "list_repo_tags",
    }
)
_GITHUB_TOOLS = frozenset(
    {
        "github_search_prs",
        "github_get_pr",
        "github_search_issues",
        "github_get_issue",
        "github_code_search",
    }
)
_SIGNOZ_TOOLS = frozenset(
    {
        "signoz_errors",
        "signoz_traceback",
        "signoz_services",
        "signoz_query",
        "signoz_list_services",
        "signoz_get_trace_details",
        "signoz_list_metrics",
        "signoz_get_field_keys",
        "signoz_get_field_values",
        "signoz_search_logs",
        "signoz_aggregate_logs",
        "signoz_search_traces",
        "signoz_aggregate_traces",
        "signoz_query_metrics",
    }
)
_ARTIFACT_TOOLS = frozenset(
    {"sandbox_list", "sandbox_search", "sandbox_read"}
)
_HISTORICAL_TOOLS = frozenset(
    {"ticket_record_search", "ticket_record_read"}
)

_DATABASE_CATEGORIES = frozenset(
    {
        "platform_issue",
        "knowledge_base",
        "workflow",
        "api",
        "permissions",
        "billing",
        "refund",
        "general",
        "mention",
    }
)
_SOURCE_CATEGORIES = frozenset(
    {
        "platform_issue",
        "knowledge_base",
        "workflow",
        "api",
        "permissions",
        "product_usage",
        "billing",
        "general",
        "mention",
    }
)
_RUNTIME_CATEGORIES = frozenset(
    {"platform_issue", "knowledge_base", "workflow", "api", "general", "mention"}
)

_RUNTIME_LANE_MARKERS = frozenset(
    {"runtime", "log", "trace", "metric", "cache", "queue", "worker", "quota"}
)
_HIGH_RISK_RUNTIME_LANE_MARKERS = frozenset(
    {"runtime", "log", "trace", "metric", "cache", "queue", "worker"}
)
_SOURCE_LANE_MARKERS = frozenset(
    {"source", "code", "version", "deployment", "implementation"}
)
_GITHUB_LANE_MARKERS = frozenset(
    {"github", "pr", "issue", "bug", "defect", "regression", "fix"}
)
_ARTIFACT_LANE_MARKERS = frozenset(
    {"artifact", "attachment", "file", "screenshot", "image"}
)
_HISTORICAL_LANE_MARKERS = frozenset(
    {"history", "historical", "lesson", "prior_ticket"}
)


@dataclass(frozen=True)
class ToolPolicyDecision:
    allowed: bool
    reason: str = ""


def current_investigation_phase(session: AgentSession) -> str:
    """Infer the phase from durable session facts instead of prompt state."""
    if session.reply_sent:
        return "complete"
    if session.context is None:
        return "intake"
    if not session.ticket_type:
        return "classify"
    if not session.investigation_plan or not session.investigation_route:
        return "plan"
    return "investigate"


def _plan_lane_text(session: AgentSession) -> str:
    plan = session.investigation_plan or {}
    values: list[str] = []
    for key in ("evidence_lanes", "independent_lanes"):
        raw = plan.get(key)
        if isinstance(raw, list):
            values.extend(str(item).strip().casefold() for item in raw if str(item).strip())
    return " ".join(values)


def _contains_lane_marker(text: str, markers: frozenset[str]) -> bool:
    return any(marker in text for marker in markers)


def _parallel_subagents_allowed(session: AgentSession) -> bool:
    if session.investigation_route not in {"standard", "deep"}:
        return False
    raw = (session.investigation_plan or {}).get("independent_lanes")
    if not isinstance(raw, list):
        return False
    lanes = {str(item).strip().casefold() for item in raw if str(item).strip()}
    return len(lanes) >= 2


def _investigation_tools(session: AgentSession) -> set[str]:
    route = session.investigation_route
    category = session.ticket_type
    lane_text = _plan_lane_text(session)
    allowed = set(_COMMON_INVESTIGATION_TOOLS)

    if route == "fast":
        if category in _SOURCE_CATEGORIES and _contains_lane_marker(
            lane_text, _SOURCE_LANE_MARKERS
        ):
            allowed.update(_SOURCE_TOOLS)
    elif route == "high_risk":
        if category in _DATABASE_CATEGORIES:
            allowed.update(_DATABASE_TOOLS)
        if category in _SOURCE_CATEGORIES and _contains_lane_marker(
            lane_text, _SOURCE_LANE_MARKERS
        ):
            allowed.update(_SOURCE_TOOLS)
        if _contains_lane_marker(lane_text, _HIGH_RISK_RUNTIME_LANE_MARKERS):
            allowed.update(_SIGNOZ_TOOLS)
        if _contains_lane_marker(lane_text, _GITHUB_LANE_MARKERS):
            allowed.update(_GITHUB_TOOLS)
        if _contains_lane_marker(lane_text, _ARTIFACT_LANE_MARKERS):
            allowed.update(_ARTIFACT_TOOLS)
        if _contains_lane_marker(lane_text, _HISTORICAL_LANE_MARKERS):
            allowed.update(_HISTORICAL_TOOLS)
    else:
        if category in _DATABASE_CATEGORIES:
            allowed.update(_DATABASE_TOOLS)
        if category in _SOURCE_CATEGORIES:
            allowed.update(_SOURCE_TOOLS)
        if category in _RUNTIME_CATEGORIES or _contains_lane_marker(
            lane_text, _RUNTIME_LANE_MARKERS
        ):
            allowed.update(_SIGNOZ_TOOLS)
        if _contains_lane_marker(lane_text, _GITHUB_LANE_MARKERS):
            allowed.update(_GITHUB_TOOLS)
        if _contains_lane_marker(lane_text, _ARTIFACT_LANE_MARKERS):
            allowed.update(_ARTIFACT_TOOLS)
        if _contains_lane_marker(lane_text, _HISTORICAL_LANE_MARKERS):
            allowed.update(_HISTORICAL_TOOLS)
        if _parallel_subagents_allowed(session):
            allowed.add("run_subagents_parallel")

    # Internal notes are a recovery path only after customer delivery failed.
    if any(
        item.get("tool") == "zendesk_public_reply"
        and item.get("success") is False
        for item in session.tool_call_log
        if isinstance(item, dict)
    ):
        allowed.add("zendesk_internal_reply")
    return allowed


def allowed_tool_names(
    session: AgentSession,
    registered_tool_names: Iterable[str],
) -> set[str]:
    """Return the provider-visible tool names for the current phase."""
    registered = set(registered_tool_names)
    if not session.investigation_policy_enabled:
        return registered

    phase = current_investigation_phase(session)
    if phase == "intake":
        allowed = _INTAKE_TOOLS
    elif phase == "classify":
        allowed = _CLASSIFY_TOOLS
    elif phase == "plan":
        allowed = _PLAN_TOOLS
    elif phase == "investigate":
        allowed = _investigation_tools(session)
    else:
        allowed = frozenset()
    return registered.intersection(allowed)


def evaluate_tool_call(
    *,
    session: AgentSession,
    tool_name: str,
) -> ToolPolicyDecision:
    """Fail closed when a provider emits a tool outside the visible surface."""
    if not session.investigation_policy_enabled:
        return ToolPolicyDecision(True)
    if tool_name in allowed_tool_names(session, {tool_name}):
        return ToolPolicyDecision(True)

    phase = current_investigation_phase(session)
    next_step = {
        "intake": "Call zendesk_get_ticket first.",
        "classify": "Classify the fresh Zendesk context before investigating.",
        "plan": "Call plan_investigation before gathering evidence.",
        "investigate": "Use only tools justified by the frozen route and evidence lanes.",
        "complete": "The customer-visible reply is already terminal.",
    }[phase]
    return ToolPolicyDecision(
        False,
        f"Tool {tool_name} is not allowed during investigation phase {phase}. {next_step}",
    )


def filter_tool_schemas(
    session: AgentSession,
    tool_schemas: Iterable[_ToolSchemaT],
) -> list[_ToolSchemaT]:
    """Filter provider schemas using the same policy as execution."""
    schemas = list(tool_schemas)
    names = {
        str(getattr(schema, "name", ""))
        for schema in schemas
        if str(getattr(schema, "name", ""))
    }
    allowed = allowed_tool_names(session, names)
    return [schema for schema in schemas if getattr(schema, "name", "") in allowed]
