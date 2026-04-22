"""Grafana tools: list dashboards / get dashboard / query Prometheus."""
from __future__ import annotations

import json
from typing import Any, TYPE_CHECKING

from zd_agent.config import get_config
from zd_agent.integrations.grafana import GrafanaClient
from zd_agent.tools.base import Tool, ToolResult

if TYPE_CHECKING:
    from zd_agent.session.agent_session import AgentSession


def _client() -> GrafanaClient:
    cfg = get_config().grafana
    if not cfg.url or not cfg.api_key:
        raise RuntimeError("Grafana not configured (missing GRAFANA_URL or GRAFANA_API_KEY)")
    return GrafanaClient(
        url=cfg.url,
        api_key=cfg.api_key,
        prom_datasource_id=cfg.prom_datasource_id,
    )


class GrafanaListDashboards(Tool):
    name = "grafana_list_dashboards"
    description = "搜索 Grafana dashboard（按标题关键词）。返回 uid + title + url 列表。"
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "标题关键词，空字符串返回前若干条"},
        },
        "required": ["query"],
    }
    group = "platform_issue"
    concurrency_safe = True

    async def execute(self, args: dict[str, Any], session: "AgentSession") -> ToolResult:
        try:
            client = _client()
            result = await client.list_dashboards(query=args.get("query", ""))
        except Exception as e:
            return ToolResult(success=False, error=str(e))
        summary = "\n".join(
            f"- {d.get('uid')} | {d.get('title')} | {d.get('url')}"
            for d in result[:50]
        )
        return ToolResult(output=summary or "(no dashboards found)")


class GrafanaGetDashboard(Tool):
    name = "grafana_get_dashboard"
    description = "按 UID 获取 Grafana dashboard 详情（含面板定义）"
    parameters = {
        "type": "object",
        "properties": {"uid": {"type": "string"}},
        "required": ["uid"],
    }
    group = "platform_issue"
    concurrency_safe = True

    async def execute(self, args: dict[str, Any], session: "AgentSession") -> ToolResult:
        try:
            client = _client()
            result = await client.get_dashboard(uid=args["uid"])
        except Exception as e:
            return ToolResult(success=False, error=str(e))
        return ToolResult(output=json.dumps(result, ensure_ascii=False, indent=2)[:10000])


class GrafanaQueryPrometheus(Tool):
    name = "grafana_query_prometheus"
    description = (
        "通过 Grafana 执行 PromQL 范围查询。start/end 支持 Grafana 相对时间（如 now-1h, now）"
        "或 ISO8601 时间戳。step 默认 15s。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "PromQL"},
            "start": {"type": "string"},
            "end": {"type": "string"},
            "step": {"type": "string", "description": "step，默认 15s"},
        },
        "required": ["query", "start", "end"],
    }
    group = "platform_issue"
    concurrency_safe = True

    async def execute(self, args: dict[str, Any], session: "AgentSession") -> ToolResult:
        try:
            client = _client()
            result = await client.query_prometheus(
                query=args["query"],
                start=args["start"],
                end=args["end"],
                step=args.get("step", "15s"),
            )
        except Exception as e:
            return ToolResult(success=False, error=str(e))
        return ToolResult(output=json.dumps(result, ensure_ascii=False, indent=2)[:10000])
