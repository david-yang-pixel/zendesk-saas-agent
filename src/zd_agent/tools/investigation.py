"""Structured investigation planning tool."""

from __future__ import annotations

from typing import Any

from zd_agent.runtime.investigation import VALID_INVESTIGATION_ROUTES
from zd_agent.session.agent_session import AgentSession
from zd_agent.tools.base import Tool, ToolResult


_HIGH_RISK_CATEGORIES = frozenset({"billing", "refund", "permissions"})
_VALID_ENVIRONMENTS = frozenset({"saas", "enterprise", "unknown"})
_MAX_PLAN_ITEMS = 12


def _string_list(
    args: dict[str, Any],
    key: str,
    *,
    allow_empty: bool = False,
) -> tuple[list[str], str | None]:
    raw = args.get(key)
    if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
        return [], f"{key} must be a list of strings"
    values = list(dict.fromkeys(item.strip() for item in raw if item.strip()))
    if not allow_empty and not values:
        return [], f"{key} must not be empty"
    if len(values) > _MAX_PLAN_ITEMS:
        return [], f"{key} must contain at most {_MAX_PLAN_ITEMS} items"
    return values, None


class PlanInvestigation(Tool):
    name = "plan_investigation"
    description = (
        "在最新 Zendesk 工单已读取并完成分类后，冻结本次调查计划。必须写明"
        "客户真实问题、环境、fast/standard/deep/high_risk 路线、对象标识、UTC "
        "时间窗、真实执行链、证据 lanes、可独立并行 lanes 和仍未知事实。计划"
        "成功后系统才会开放与类别和路线匹配的证据工具。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "route": {
                "type": "string",
                "enum": sorted(VALID_INVESTIGATION_ROUTES),
            },
            "customer_question": {"type": "string"},
            "environment": {
                "type": "string",
                "enum": sorted(_VALID_ENVIRONMENTS),
            },
            "object_identifiers": {
                "type": "array",
                "minItems": 1,
                "items": {"type": "string"},
            },
            "time_window": {"type": "string"},
            "execution_chain": {
                "type": "array",
                "minItems": 2,
                "items": {"type": "string"},
            },
            "evidence_lanes": {
                "type": "array",
                "minItems": 1,
                "items": {"type": "string"},
            },
            "independent_lanes": {
                "type": "array",
                "items": {"type": "string"},
            },
            "unresolved_facts": {
                "type": "array",
                "items": {"type": "string"},
            },
        },
        "required": [
            "route",
            "customer_question",
            "environment",
            "object_identifiers",
            "time_window",
            "execution_chain",
            "evidence_lanes",
            "independent_lanes",
            "unresolved_facts",
        ],
    }
    group = "workflow"

    async def execute(
        self,
        args: dict[str, Any],
        session: AgentSession,
    ) -> ToolResult:
        if session.context is None or not session.ticket_type:
            return ToolResult(
                success=False,
                error=(
                    "Fresh Zendesk context and classification are required before "
                    "planning. Call zendesk_get_ticket, then classify_ticket."
                ),
            )

        route = str(args.get("route") or "").strip()
        if route not in VALID_INVESTIGATION_ROUTES:
            return ToolResult(success=False, error=f"invalid route: {route!r}")
        if session.ticket_type in _HIGH_RISK_CATEGORIES and route != "high_risk":
            return ToolResult(
                success=False,
                error=(
                    f"ticket_type={session.ticket_type} must use route=high_risk "
                    "for identity, authorization, and business-decision boundaries"
                ),
            )

        customer_question = str(args.get("customer_question") or "").strip()
        environment = str(args.get("environment") or "").strip()
        time_window = str(args.get("time_window") or "").strip()
        if not customer_question:
            return ToolResult(success=False, error="customer_question is required")
        if environment not in _VALID_ENVIRONMENTS:
            return ToolResult(success=False, error=f"invalid environment: {environment!r}")
        if not time_window:
            return ToolResult(success=False, error="time_window is required")

        parsed: dict[str, list[str]] = {}
        for key, allow_empty in (
            ("object_identifiers", False),
            ("execution_chain", False),
            ("evidence_lanes", False),
            ("independent_lanes", True),
            ("unresolved_facts", True),
        ):
            values, error = _string_list(args, key, allow_empty=allow_empty)
            if error:
                return ToolResult(success=False, error=error)
            parsed[key] = values

        if len(parsed["execution_chain"]) < 2:
            return ToolResult(
                success=False,
                error="execution_chain must contain at least two ordered hops",
            )
        if route == "fast" and parsed["independent_lanes"]:
            return ToolResult(
                success=False,
                error="fast route must not dispatch independent subagent lanes",
            )
        if route == "deep":
            lane_text = " ".join(parsed["evidence_lanes"]).casefold()
            missing = [
                lane
                for lane in ("impact_scope", "baseline")
                if lane not in lane_text
            ]
            if missing:
                return ToolResult(
                    success=False,
                    error=(
                        "deep route evidence_lanes must include impact_scope and "
                        "baseline; missing: " + ", ".join(missing)
                    ),
                )

        plan: dict[str, Any] = {
            "route": route,
            "customer_question": customer_question,
            "environment": environment,
            "object_identifiers": parsed["object_identifiers"],
            "time_window": time_window,
            "execution_chain": parsed["execution_chain"],
            "evidence_lanes": parsed["evidence_lanes"],
            "independent_lanes": parsed["independent_lanes"],
            "unresolved_facts": parsed["unresolved_facts"],
        }
        session.investigation_route = route
        session.investigation_plan = plan
        return ToolResult(
            output=(
                f"Investigation plan accepted: route={route}, "
                f"category={session.ticket_type}, "
                f"evidence_lanes={len(parsed['evidence_lanes'])}, "
                f"independent_lanes={len(parsed['independent_lanes'])}. "
                "Proceed only with tools exposed for this plan."
            ),
            metadata={"investigation_plan": plan},
        )

